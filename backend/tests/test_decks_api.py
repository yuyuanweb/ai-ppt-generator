import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_queue
from app.core.db import async_session_factory
from app.domain.flex_layout import FlexContainer, FlexLeaf
from app.domain.layout import get_layout
from app.domain.slide_draft import (
    BulletsContent,
    FlexBlockContent,
    FlexBulletsContent,
    FlexImageContent,
    FlexKpiContent,
    FlexSlideDraft,
    FlexTableContent,
    FlexTextContent,
    ImageContent,
    KpiContent,
    SlideDraft,
    SlotContent,
    TableContent,
    TextContent,
)
from app.llm.base import SlideGenerationInput
from app.llm.errors import InvalidSlideOutputError
from app.main import app
from app.models.project import Project
from app.models.slide import Slide
from app.services.deck import clear_cancel, request_cancel
from app.worker.deck_tasks import generate_deck


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def enqueue_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


class FakeSlideGenerator:
    """按布局槽位机械填充内容，用于验证编排而不是模型质量。"""

    def __init__(self, *, fail_positions: set[int] | None = None) -> None:
        self.fail_positions = fail_positions or set()
        self.seen: list[int] = []

    async def generate(self, payload: SlideGenerationInput) -> SlideDraft | FlexSlideDraft:
        self.seen.append(payload.position)
        if payload.position in self.fail_positions:
            raise InvalidSlideOutputError("模拟生成失败")
        if payload.layout_mode == "flex":
            return _fake_flex_draft(payload.layout_id)
        layout = get_layout(payload.layout_id)
        return SlideDraft(
            blocks=[_fill(slot.id, slot.accepts[0]) for slot in layout.slots if slot.required],
            speaker_notes="讲稿提示",
        )


_RICH_BULLETS = [
    "交付周期从六周缩短到三周，瓶颈在评审排队",
    "重复建设占比过高，跨团队接口缺少统一契约",
    "线上故障平均恢复时间仍超过四小时，需专人值班",
]


def _fill(slot_id: str, block_type: str) -> SlotContent:
    match block_type:
        case "bullets":
            return BulletsContent(slot_id=slot_id, items=list(_RICH_BULLETS))
        case "image":
            return ImageContent(slot_id=slot_id, alt="示意图")
        case "kpi":
            return KpiContent(slot_id=slot_id, value="37%", label="增长")
        case "table":
            return TableContent(slot_id=slot_id, header=["项目", "结果"], rows=[["一", "二"]])
        case _:
            return TextContent(slot_id=slot_id, text="本页核心结论与行动建议")


def _fill_flex(block_id: str, block_type: str) -> FlexBlockContent:
    match block_type:
        case "bullets":
            return FlexBulletsContent(id=block_id, items=list(_RICH_BULLETS))
        case "image":
            return FlexImageContent(id=block_id, alt="示意图")
        case "kpi":
            return FlexKpiContent(id=block_id, value="37%", label="增长")
        case "table":
            return FlexTableContent(id=block_id, header=["项目", "结果"], rows=[["一", "二"]])
        case _:
            return FlexTextContent(id=block_id, text="本页核心结论与行动建议")


def _fake_flex_draft(layout_id: str) -> FlexSlideDraft:
    """产出足够充实的多块 flex 页，避免触发过瘦 repair。"""
    blocks: list[FlexBlockContent] = [
        FlexTextContent(id="title", text="本页核心结论与行动建议"),
        FlexBulletsContent(id="body", items=list(_RICH_BULLETS)),
        FlexKpiContent(id="kpi_1", value="37%", label="增长"),
        FlexImageContent(id="visual", alt="示意图"),
    ]
    tree = FlexContainer(
        type="column",
        id="root",
        children=[FlexLeaf(id=f"leaf-{block.id}", block_id=block.id, grow=1.0) for block in blocks],
    )
    return FlexSlideDraft(blocks=blocks, layout_tree=tree, speaker_notes="讲稿提示")


@pytest.fixture
async def queue(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[FakeQueue, None]:
    fake = FakeQueue()

    async def ignore(*_args, **_kwargs) -> None:
        pass

    monkeypatch.setattr("app.api.v1.outlines.publish_outline_event", ignore)
    app.dependency_overrides[get_queue] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_queue, None)


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


async def _sign_up(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": f"deck_{uuid.uuid4().hex}@example.com", "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _pages(count: int) -> list[dict]:
    return [
        {
            "id": str(uuid.uuid4()),
            "title": f"第 {index} 页",
            "objective": "说明本页的核心目标",
            "key_points": ["要点一", "要点二"],
            "source_refs": ["S1:1"],
            "layout_id": "cover" if index == 1 else "bullets",
            "page_role": "cover" if index == 1 else "content",
        }
        for index in range(1, count + 1)
    ]


async def _confirmed_project(
    client: AsyncClient,
    headers: dict[str, str],
    pages: int = 5,
    *,
    layout_mode: str = "fixed",
) -> dict:
    """直接构造一个大纲已确认的项目，页面生成的测试不重复走大纲链路。"""
    response = await client.post(
        "/api/v1/projects",
        json={"title": "平台化复盘", "page_count": pages, "layout_mode": layout_mode},
        headers=headers,
    )
    project = response.json()
    await client.post(
        f"/api/v1/projects/{project['id']}/sources",
        json={"kind": "topic", "content": "把内部工具沉淀为平台能力"},
        headers=headers,
    )

    async with async_session_factory() as session:
        result = await session.execute(
            select(Project)
            .options(selectinload(Project.outline))
            .where(Project.id == uuid.UUID(project["id"]))
        )
        record = result.scalar_one()
        from app.models.project import ProjectOutline

        outline = ProjectOutline(
            project_id=record.id,
            status="confirmed",
            pages=_pages(pages),
            revision=2,
        )
        session.add(outline)
        record.status = "outline_ready"
        await session.commit()
    return project


@pytest.mark.asyncio
async def test_generate_requires_confirmed_outline(client: AsyncClient, queue: FakeQueue) -> None:
    headers = await _sign_up(client)
    response = await client.post("/api/v1/projects", json={"title": "无大纲"}, headers=headers)
    project = response.json()

    result = await client.post(
        f"/api/v1/projects/{project['id']}/deck/generate",
        json={},
        headers=headers,
    )
    assert result.status_code == 409
    assert queue.calls == []


@pytest.mark.asyncio
async def test_generate_materializes_pending_slides(client: AsyncClient, queue: FakeQueue) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers)

    accepted = await client.post(
        f"/api/v1/projects/{project['id']}/deck/generate",
        json={},
        headers=headers,
    )
    assert accepted.status_code == 202
    assert accepted.json()["pending"] == 5

    args, _ = queue.calls[0]
    assert args[0] == "generate_deck"
    assert len(args[2]) == 5

    deck = await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)
    # 任务已入队、worker 尚未领页：靠 project.generating + pending 页识别为 generating
    assert deck.json()["status"] == "generating"
    assert [slide["position"] for slide in deck.json()["slides"]] == [1, 2, 3, 4, 5]
    assert all(slide["status"] == "pending" for slide in deck.json()["slides"])


async def _project_status(project_id: str) -> str:
    async with async_session_factory() as session:
        record = await session.get(Project, uuid.UUID(project_id))
        assert record is not None
        return record.status


@pytest.mark.asyncio
async def test_generate_restores_status_when_enqueue_fails(
    client: AsyncClient, queue: FakeQueue
) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers)
    assert await _project_status(project["id"]) == "outline_ready"

    async def boom(*_args, **_kwargs):
        raise RedisError("redis down")

    queue.enqueue_job = boom  # type: ignore[method-assign]
    result = await client.post(
        f"/api/v1/projects/{project['id']}/deck/generate",
        json={},
        headers=headers,
    )
    assert result.status_code == 503
    assert result.json()["detail"] == "任务队列暂时不可用"
    assert await _project_status(project["id"]) == "outline_ready"


@pytest.mark.asyncio
async def test_retry_restores_status_when_enqueue_fails(
    client: AsyncClient, queue: FakeQueue
) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers)
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    slide_ids = queue.calls[0][0][2]
    await generate_deck({"slide_generator": FakeSlideGenerator()}, project["id"], slide_ids)
    assert await _project_status(project["id"]) == "ready"

    deck = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    target_id = deck["slides"][0]["id"]

    async def boom(*_args, **_kwargs):
        raise RedisError("redis down")

    queue.enqueue_job = boom  # type: ignore[method-assign]
    result = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides/{target_id}/retry",
        headers=headers,
    )
    assert result.status_code == 503
    assert result.json()["detail"] == "任务队列暂时不可用"
    assert await _project_status(project["id"]) == "ready"


@pytest.mark.asyncio
async def test_retry_clears_layout_tree(client: AsyncClient, queue: FakeQueue) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers, layout_mode="flex")
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    slide_ids = queue.calls[0][0][2]
    await generate_deck({"slide_generator": FakeSlideGenerator()}, project["id"], slide_ids)

    deck = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    target = deck["slides"][0]
    assert target["layout_tree"] is not None
    assert target["blocks"]

    retry = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides/{target['id']}/retry",
        headers=headers,
    )
    assert retry.status_code == 202

    async with async_session_factory() as session:
        slide = await session.get(Slide, uuid.UUID(target["id"]))
        assert slide is not None
        assert slide.layout_tree is None
        assert slide.blocks == []
        assert slide.status == "pending"


@pytest.mark.asyncio
async def test_worker_fills_blocks_and_marks_ready(client: AsyncClient, queue: FakeQueue) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers)
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    slide_ids = queue.calls[0][0][2]

    await generate_deck({"slide_generator": FakeSlideGenerator()}, project["id"], slide_ids)

    deck = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    assert deck["status"] == "ready"
    assert deck["ready"] == 5
    first = deck["slides"][0]
    assert first["layout_id"] == "cover"
    assert first["layout_mode"] == "fixed"
    assert first["blocks"]
    assert first["speaker_notes"] == "讲稿提示"
    assert first["issues"] == []


@pytest.mark.asyncio
async def test_worker_flex_generation_persists_layout_tree(
    client: AsyncClient, queue: FakeQueue
) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers, layout_mode="flex")
    assert project["layout_mode"] == "flex"
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    slide_ids = queue.calls[0][0][2]

    await generate_deck({"slide_generator": FakeSlideGenerator()}, project["id"], slide_ids)

    deck = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    assert deck["status"] == "ready"
    first = deck["slides"][0]
    assert first["layout_mode"] == "flex"
    assert first["layout_tree"] is not None
    assert first["layout_tree"]["type"] in {"row", "column"}
    assert first["blocks"]


@pytest.mark.asyncio
async def test_flex_project_heals_fixed_slides_on_generate(
    client: AsyncClient, queue: FakeQueue
) -> None:
    """flex 项目里残留的 fixed/无树页，再次生成时应被重置并产出 layout_tree。"""
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers, layout_mode="flex", pages=5)
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    slide_ids = queue.calls[0][0][2]
    await generate_deck({"slide_generator": FakeSlideGenerator()}, project["id"], slide_ids)

    deck = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    broken_id = deck["slides"][0]["id"]

    async with async_session_factory() as session:
        from app.models.slide import Slide

        slide = await session.get(Slide, uuid.UUID(broken_id))
        assert slide is not None
        slide.layout_mode = "fixed"
        slide.layout_tree = None
        await session.commit()

    queue.calls.clear()
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    assert queue.calls, "应重新入队待愈页"
    pending_ids = queue.calls[0][0][2]
    assert broken_id in {str(item) for item in pending_ids}

    await generate_deck({"slide_generator": FakeSlideGenerator()}, project["id"], pending_ids)
    healed = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    first = next(item for item in healed["slides"] if item["id"] == broken_id)
    assert first["layout_mode"] == "flex"
    assert first["layout_tree"] is not None


@pytest.mark.asyncio
async def test_generate_resumes_and_retries_failed_slide(
    client: AsyncClient,
    queue: FakeQueue,
) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers)
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    slide_ids = queue.calls[0][0][2]

    await generate_deck(
        {"slide_generator": FakeSlideGenerator(fail_positions={2})},
        project["id"],
        slide_ids,
    )
    deck = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    assert deck["status"] == "partial"
    assert deck["failed"] == 1
    failed = next(slide for slide in deck["slides"] if slide["status"] == "failed")

    # 续跑只挑没就绪的页，已完成的页不重复计费
    resumed = await client.post(
        f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers
    )
    assert resumed.json()["pending"] == 1

    retry = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides/{failed['id']}/retry",
        headers=headers,
    )
    assert retry.status_code == 202
    generator = FakeSlideGenerator()
    await generate_deck({"slide_generator": generator}, project["id"], [failed["id"]])
    assert generator.seen == [2]

    deck = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    assert deck["status"] == "ready"


@pytest.mark.asyncio
async def test_regenerate_all_resets_ready_slides(client: AsyncClient, queue: FakeQueue) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers)
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    await generate_deck(
        {"slide_generator": FakeSlideGenerator()}, project["id"], queue.calls[0][0][2]
    )

    blocked = await client.post(
        f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers
    )
    assert blocked.status_code == 409

    redo = await client.post(
        f"/api/v1/projects/{project['id']}/deck/generate",
        json={"regenerate_all": True},
        headers=headers,
    )
    assert redo.json()["pending"] == 5


@pytest.mark.asyncio
async def test_cancel_skips_remaining_slides(client: AsyncClient, queue: FakeQueue) -> None:
    headers = await _sign_up(client)
    project = await _confirmed_project(client, headers)
    await client.post(f"/api/v1/projects/{project['id']}/deck/generate", json={}, headers=headers)
    slide_ids = queue.calls[0][0][2]

    await request_cancel(uuid.UUID(project["id"]))
    generator = FakeSlideGenerator()
    await generate_deck({"slide_generator": generator}, project["id"], slide_ids)

    assert generator.seen == []
    deck = (await client.get(f"/api/v1/projects/{project['id']}/deck", headers=headers)).json()
    assert deck["ready"] == 0
    await clear_cancel(uuid.UUID(project["id"]))


@pytest.mark.asyncio
async def test_deck_is_isolated_between_users(client: AsyncClient, queue: FakeQueue) -> None:
    owner = await _sign_up(client)
    other = await _sign_up(client)
    project = await _confirmed_project(client, owner)

    response = await client.get(f"/api/v1/projects/{project['id']}/deck", headers=other)
    assert response.status_code == 404
