import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db import async_session_factory
from app.main import app
from app.models.project import Project, ProjectOutline
from app.models.slide import Slide as SlideRow
from app.schemas.project import MAX_DECK_PAGE_COUNT
from app.services.deck import load_slides, sync_slides


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


async def _sign_up(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": f"pages_{uuid.uuid4().hex}@example.com", "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _blocks(title: str) -> list[dict]:
    return [
        {
            "id": f"{title}-t",
            "slot_id": f"{title}-t",
            "type": "text",
            "text": title,
            "locked": False,
        },
        {
            "id": f"{title}-b",
            "slot_id": f"{title}-b",
            "type": "bullets",
            "items": ["要点一", "要点二"],
            "locked": False,
        },
    ]


def _tree(title: str) -> dict:
    return {
        "type": "column",
        "id": "root",
        "gap_pt": 16.0,
        "grow": 1.0,
        "children": [
            {
                "type": "block",
                "id": f"leaf-{title}-t",
                "block_id": f"{title}-t",
                "grow": 0.45,
                "text_style": "title",
            },
            {
                "type": "block",
                "id": f"leaf-{title}-b",
                "block_id": f"{title}-b",
                "grow": 1.5,
                "text_style": "bullet",
            },
        ],
    }


async def _project_with_pages(
    client: AsyncClient,
    headers: dict[str, str],
    titles: list[str],
    *,
    status: str = "ready",
) -> tuple[dict, list[SlideRow]]:
    """建一个已确认大纲的项目，每个标题一页 flex 内容。"""
    response = await client.post(
        "/api/v1/projects",
        json={"title": "页面增删", "page_count": 5},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    project = response.json()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Project)
            .options(selectinload(Project.outline))
            .where(Project.id == uuid.UUID(project["id"]))
        )
        record = result.scalar_one()
        pages: list[dict] = []
        rows: list[SlideRow] = []
        for index, title in enumerate(titles, start=1):
            page_id = uuid.uuid4()
            pages.append(
                {
                    "id": str(page_id),
                    "title": title,
                    "objective": "目标",
                    "key_points": ["要点一", "要点二"],
                    "source_refs": [],
                    "layout_id": "bullets",
                    "page_role": "content",
                }
            )
            row = SlideRow(
                project_id=record.id,
                outline_page_id=page_id,
                position=index,
                layout_id="bullets",
                layout_mode="flex",
                layout_tree=_tree(title),
                title=title,
                status=status,
                blocks=_blocks(title),
                issues=[],
            )
            session.add(row)
            rows.append(row)

        session.add(
            ProjectOutline(
                project_id=record.id,
                status="confirmed",
                pages=pages,
                revision=2,
            )
        )
        record.status = "ready"
        await session.commit()
        for row in rows:
            await session.refresh(row)
        return project, rows


async def _load_state(project_id: str) -> tuple[Project, list[dict], list[SlideRow]]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Project)
            .options(selectinload(Project.outline))
            .where(Project.id == uuid.UUID(project_id))
        )
        record = result.scalar_one()
        slides = await load_slides(session, record.id)
        assert record.outline is not None
        return record, list(record.outline.pages), slides


def _assert_outline_matches_slides(pages: list[dict], slides: list[SlideRow]) -> None:
    assert [page["id"] for page in pages] == [str(slide.outline_page_id) for slide in slides]


@pytest.mark.asyncio
async def test_insert_blank_page_after_given_slide(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides",
        headers=headers,
        json={"after_slide_id": str(slides[0].id)},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    created = body["slide_id"]
    ids = [slide["id"] for slide in body["deck"]["slides"]]
    assert ids == [str(slides[0].id), created, str(slides[1].id)]
    assert [slide["position"] for slide in body["deck"]["slides"]] == [1, 2, 3]
    assert body["deck"]["total"] == 3

    new_page = next(slide for slide in body["deck"]["slides"] if slide["id"] == created)
    assert new_page["status"] == "ready"
    assert [block["type"] for block in new_page["blocks"]] == ["text", "bullets"]
    assert new_page["layout_mode"] == "flex"
    assert new_page["layout_tree"] is not None


@pytest.mark.asyncio
async def test_insert_without_target_appends_to_the_end(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides",
        headers=headers,
        json={},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert [slide["id"] for slide in body["deck"]["slides"]][-1] == body["slide_id"]


@pytest.mark.asyncio
async def test_insert_syncs_outline_and_page_count(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides",
        headers=headers,
        json={"after_slide_id": str(slides[0].id)},
    )
    assert response.status_code == 201

    record, pages, rows = await _load_state(project["id"])
    _assert_outline_matches_slides(pages, rows)
    assert record.page_count == 3
    inserted = pages[1]
    assert inserted["layout_id"] == "bullets"
    assert len(inserted["key_points"]) >= 2


@pytest.mark.asyncio
async def test_inserted_page_survives_outline_sync(client: AsyncClient) -> None:
    """sync_slides 会删掉大纲里不存在的页，插入必须同时写进大纲。"""
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides",
        headers=headers,
        json={"after_slide_id": str(slides[0].id)},
    )
    created = response.json()["slide_id"]

    async with async_session_factory() as session:
        result = await session.execute(
            select(Project)
            .options(selectinload(Project.outline))
            .where(Project.id == uuid.UUID(project["id"]))
        )
        record = result.scalar_one()
        await sync_slides(session, record)
        await session.commit()

    _, pages, rows = await _load_state(project["id"])
    assert created in {str(slide.id) for slide in rows}
    assert [slide.position for slide in rows] == [1, 2, 3]
    _assert_outline_matches_slides(pages, rows)


@pytest.mark.asyncio
async def test_duplicate_page_copies_content_with_new_block_ids(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二"])
    source = slides[0]

    response = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides/{source.id}/duplicate",
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    copy_id = body["slide_id"]
    ids = [slide["id"] for slide in body["deck"]["slides"]]
    assert ids == [str(source.id), copy_id, str(slides[1].id)]

    copy = next(slide for slide in body["deck"]["slides"] if slide["id"] == copy_id)
    assert copy["title"] == source.title
    assert [block["type"] for block in copy["blocks"]] == ["text", "bullets"]
    assert copy["blocks"][0]["text"] == source.blocks[0]["text"]
    assert {block["id"] for block in copy["blocks"]}.isdisjoint(
        block["id"] for block in source.blocks
    )
    leaves = [child["block_id"] for child in copy["layout_tree"]["children"]]
    assert leaves == [block["id"] for block in copy["blocks"]]


@pytest.mark.asyncio
async def test_duplicate_page_gets_its_own_outline_page(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides/{slides[0].id}/duplicate",
        headers=headers,
    )
    assert response.status_code == 201

    record, pages, rows = await _load_state(project["id"])
    _assert_outline_matches_slides(pages, rows)
    assert record.page_count == 3
    # 幂等键必须唯一，否则 uq_slides_project_outline_page 会挡住写入
    assert len({slide.outline_page_id for slide in rows}) == 3
    assert pages[0]["title"] == pages[1]["title"]


@pytest.mark.asyncio
async def test_delete_page_renumbers_and_focuses_next(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二", "三"])

    response = await client.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/deck/slides/{slides[1].id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slide_id"] == str(slides[2].id)
    assert [slide["id"] for slide in body["deck"]["slides"]] == [
        str(slides[0].id),
        str(slides[2].id),
    ]
    assert [slide["position"] for slide in body["deck"]["slides"]] == [1, 2]

    record, pages, rows = await _load_state(project["id"])
    _assert_outline_matches_slides(pages, rows)
    assert record.page_count == 2


@pytest.mark.asyncio
async def test_deleting_last_page_focuses_previous(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二"])

    response = await client.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/deck/slides/{slides[1].id}",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["slide_id"] == str(slides[0].id)


@pytest.mark.asyncio
async def test_delete_keeps_at_least_one_page(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["唯一"])

    response = await client.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/deck/slides/{slides[0].id}",
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "至少保留一页"


@pytest.mark.asyncio
async def test_page_operations_reject_while_generating(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二"], status="generating")

    insert = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides", headers=headers, json={}
    )
    duplicate = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides/{slides[0].id}/duplicate",
        headers=headers,
    )
    removed = await client.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/deck/slides/{slides[0].id}",
        headers=headers,
    )
    assert [insert.status_code, duplicate.status_code, removed.status_code] == [409] * 3


@pytest.mark.asyncio
async def test_page_operations_reject_unknown_slide(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, _ = await _project_with_pages(client, headers, ["一"])
    missing = uuid.uuid4()

    insert = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides",
        headers=headers,
        json={"after_slide_id": str(missing)},
    )
    duplicate = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides/{missing}/duplicate",
        headers=headers,
    )
    removed = await client.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/deck/slides/{missing}",
        headers=headers,
    )
    assert [insert.status_code, duplicate.status_code, removed.status_code] == [404] * 3


@pytest.mark.asyncio
async def test_insert_rejects_project_without_outline(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    created = await client.post(
        "/api/v1/projects",
        json={"title": "还没有大纲", "page_count": 5},
        headers=headers,
    )
    project = created.json()

    response = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides", headers=headers, json={}
    )
    assert response.status_code == 409
    assert "大纲" in response.json()["detail"]


@pytest.mark.asyncio
async def test_insert_rejects_beyond_page_limit(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    titles = [f"第{index}页" for index in range(MAX_DECK_PAGE_COUNT)]
    project, _ = await _project_with_pages(client, headers, titles)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/deck/slides", headers=headers, json={}
    )
    assert response.status_code == 422
    assert str(MAX_DECK_PAGE_COUNT) in response.json()["detail"]


@pytest.mark.asyncio
async def test_page_change_realigns_outline_after_reorder(client: AsyncClient) -> None:
    """拖拽排序只改 position；整页操作顺手把大纲页序对齐到实际页序。"""
    headers = await _sign_up(client)
    project, slides = await _project_with_pages(client, headers, ["一", "二", "三"])
    reordered = [slides[2].id, slides[0].id, slides[1].id]

    order = await client.put(
        f"/api/v1/projects/{project['id']}/deck/slides/order",
        headers=headers,
        json={"slide_ids": [str(item) for item in reordered]},
    )
    assert order.status_code == 200

    response = await client.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/deck/slides/{slides[0].id}",
        headers=headers,
    )
    assert response.status_code == 200

    _, pages, rows = await _load_state(project["id"])
    assert [slide.id for slide in rows] == [slides[2].id, slides[1].id]
    _assert_outline_matches_slides(pages, rows)
