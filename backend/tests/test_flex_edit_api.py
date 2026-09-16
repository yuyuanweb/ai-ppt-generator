import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db import async_session_factory
from app.domain.flex_layout import FlexContainer, FlexLeaf, iter_leaf_block_ids
from app.main import app
from app.models.project import Project, ProjectOutline
from app.models.slide import Slide as SlideRow


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


async def _sign_up(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": f"flex_{uuid.uuid4().hex}@example.com", "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _project_with_slides(
    client: AsyncClient,
    headers: dict[str, str],
    slides: list[dict],
) -> tuple[dict, list[SlideRow]]:
    response = await client.post(
        "/api/v1/projects",
        json={"title": "灵活布局编辑", "page_count": 5},
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
        pages = []
        rows: list[SlideRow] = []
        for index, spec in enumerate(slides, start=1):
            page_id = uuid.uuid4()
            pages.append(
                {
                    "id": str(page_id),
                    "title": spec["title"],
                    "objective": "目标",
                    "key_points": ["要点"],
                    "source_refs": [],
                    "layout_id": spec["layout_id"],
                }
            )
            row = SlideRow(
                project_id=record.id,
                outline_page_id=page_id,
                position=index,
                layout_id=spec["layout_id"],
                title=spec["title"],
                status=spec.get("status", "ready"),
                blocks=spec["blocks"],
                issues=spec.get("issues", []),
                revision=spec.get("revision", 1),
                layout_mode=spec.get("layout_mode", "fixed"),
                layout_tree=spec.get("layout_tree"),
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


def _bullets_blocks(*, title: str = "标题") -> list[dict]:
    return [
        {
            "id": "t1",
            "slot_id": "title",
            "type": "text",
            "text": title,
            "locked": False,
        },
        {
            "id": "b1",
            "slot_id": "body",
            "type": "bullets",
            "items": ["要点一", "要点二"],
            "locked": False,
        },
    ]


def _deck_url(project_id: str, slide_id: uuid.UUID, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/deck/slides/{slide_id}{suffix}"


@pytest.mark.asyncio
async def test_unlock_fixed_bullets_to_flex(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_slides(
        client,
        headers,
        [{"title": "要点页", "layout_id": "bullets", "blocks": _bullets_blocks()}],
    )
    slide = slides[0]

    response = await client.post(
        _deck_url(project["id"], slide.id, "/unlock-flex"),
        headers=headers,
        json={"revision": slide.revision},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["layout_mode"] == "flex"
    assert body["layout_id"] == "bullets"
    assert body["revision"] == slide.revision + 1
    tree = FlexContainer.model_validate(body["layout_tree"])
    assert tree.type == "column"
    assert tree.id == "root"
    leaf_ids = iter_leaf_block_ids(tree)
    assert set(leaf_ids) == {"t1", "b1"}
    leaves = [
        child
        for row in tree.children
        if isinstance(row, FlexContainer)
        for child in row.children
        if isinstance(child, FlexLeaf)
    ]
    by_block = {leaf.block_id: leaf for leaf in leaves}
    assert by_block["t1"].id == "leaf-t1"
    assert by_block["t1"].text_style == "title"
    assert by_block["b1"].id == "leaf-b1"
    assert by_block["b1"].text_style == "bullet"


@pytest.mark.asyncio
async def test_add_block_on_flex_slide(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_slides(
        client,
        headers,
        [{"title": "要点页", "layout_id": "bullets", "blocks": _bullets_blocks()}],
    )
    slide = slides[0]
    unlocked = await client.post(
        _deck_url(project["id"], slide.id, "/unlock-flex"),
        headers=headers,
        json={"revision": slide.revision},
    )
    assert unlocked.status_code == 200
    current = unlocked.json()
    tree = FlexContainer.model_validate(current["layout_tree"])
    parent_id = tree.children[1].id if tree.children else tree.id

    response = await client.post(
        _deck_url(project["id"], slide.id, "/blocks"),
        headers=headers,
        json={
            "revision": current["revision"],
            "type": "text",
            "parent_id": parent_id,
            "index": 1,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == current["revision"] + 1
    assert len(body["blocks"]) == 3
    added = next(block for block in body["blocks"] if block["id"] not in {"t1", "b1"})
    assert added["type"] == "text"
    assert added["text"] == "新文本"
    assert added["slot_id"] == added["id"]
    leaf_ids = iter_leaf_block_ids(FlexContainer.model_validate(body["layout_tree"]))
    assert added["id"] in leaf_ids


@pytest.mark.asyncio
async def test_delete_block_on_flex_slide(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_slides(
        client,
        headers,
        [{"title": "要点页", "layout_id": "bullets", "blocks": _bullets_blocks()}],
    )
    slide = slides[0]
    unlocked = await client.post(
        _deck_url(project["id"], slide.id, "/unlock-flex"),
        headers=headers,
        json={"revision": slide.revision},
    )
    current = unlocked.json()
    tree = FlexContainer.model_validate(current["layout_tree"])
    parent_id = tree.children[1].id

    created = await client.post(
        _deck_url(project["id"], slide.id, "/blocks"),
        headers=headers,
        json={
            "revision": current["revision"],
            "type": "kpi",
            "parent_id": parent_id,
            "index": 1,
        },
    )
    assert created.status_code == 200
    current = created.json()
    extra_id = next(block["id"] for block in current["blocks"] if block["id"] not in {"t1", "b1"})

    response = await client.request(
        "DELETE",
        _deck_url(project["id"], slide.id, f"/blocks/{extra_id}"),
        headers=headers,
        json={"revision": current["revision"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == current["revision"] + 1
    assert {block["id"] for block in body["blocks"]} == {"t1", "b1"}
    assert set(iter_leaf_block_ids(FlexContainer.model_validate(body["layout_tree"]))) == {
        "t1",
        "b1",
    }


@pytest.mark.asyncio
async def test_put_flex_state_restores_blocks_and_tree(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_slides(
        client,
        headers,
        [{"title": "要点页", "layout_id": "bullets", "blocks": _bullets_blocks()}],
    )
    slide = slides[0]
    unlocked = await client.post(
        _deck_url(project["id"], slide.id, "/unlock-flex"),
        headers=headers,
        json={"revision": slide.revision},
    )
    current = unlocked.json()
    tree = FlexContainer.model_validate(current["layout_tree"])
    snapshot_blocks = current["blocks"]
    snapshot_tree = tree.model_dump(mode="json")

    deleted = await client.request(
        "DELETE",
        _deck_url(project["id"], slide.id, "/blocks/b1"),
        headers=headers,
        json={"revision": current["revision"]},
    )
    assert deleted.status_code == 200, deleted.text
    after_delete = deleted.json()

    restored = await client.put(
        _deck_url(project["id"], slide.id, "/flex-state"),
        headers=headers,
        json={
            "revision": after_delete["revision"],
            "blocks": snapshot_blocks,
            "layout_tree": snapshot_tree,
        },
    )
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert {block["id"] for block in body["blocks"]} == {"t1", "b1"}
    assert set(iter_leaf_block_ids(FlexContainer.model_validate(body["layout_tree"]))) == {
        "t1",
        "b1",
    }


@pytest.mark.asyncio
async def test_put_flex_layout_reorders_leaves(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_slides(
        client,
        headers,
        [{"title": "要点页", "layout_id": "bullets", "blocks": _bullets_blocks()}],
    )
    slide = slides[0]
    unlocked = await client.post(
        _deck_url(project["id"], slide.id, "/unlock-flex"),
        headers=headers,
        json={"revision": slide.revision},
    )
    current = unlocked.json()
    tree = FlexContainer.model_validate(current["layout_tree"])
    # 把两行对调：body 在上、title 在下
    reordered = FlexContainer(
        type="column",
        id="root",
        children=list(reversed(tree.children)),
    )

    response = await client.put(
        _deck_url(project["id"], slide.id, "/flex-layout"),
        headers=headers,
        json={"revision": current["revision"], "layout_tree": reordered.model_dump(mode="json")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == current["revision"] + 1
    new_tree = FlexContainer.model_validate(body["layout_tree"])
    assert isinstance(new_tree.children[0], FlexContainer)
    first_row_leaves = [
        child.block_id for child in new_tree.children[0].children if isinstance(child, FlexLeaf)
    ]
    assert first_row_leaves == ["b1"]


@pytest.mark.asyncio
async def test_flex_ops_reject_fixed_without_unlock(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_slides(
        client,
        headers,
        [{"title": "要点页", "layout_id": "bullets", "blocks": _bullets_blocks()}],
    )
    slide = slides[0]

    add = await client.post(
        _deck_url(project["id"], slide.id, "/blocks"),
        headers=headers,
        json={
            "revision": slide.revision,
            "type": "text",
            "parent_id": "root",
            "index": 0,
        },
    )
    assert add.status_code == 409

    delete = await client.request(
        "DELETE",
        _deck_url(project["id"], slide.id, "/blocks/t1"),
        headers=headers,
        json={"revision": slide.revision},
    )
    assert delete.status_code == 409

    put = await client.put(
        _deck_url(project["id"], slide.id, "/flex-layout"),
        headers=headers,
        json={
            "revision": slide.revision,
            "layout_tree": {
                "type": "column",
                "id": "root",
                "children": [
                    {
                        "type": "block",
                        "id": "leaf-t1",
                        "block_id": "t1",
                    }
                ],
            },
        },
    )
    assert put.status_code == 409


def _simple_flex_tree() -> dict:
    return {
        "type": "column",
        "id": "root",
        "gap_pt": 16,
        "children": [
            {
                "type": "block",
                "id": "leaf-t1",
                "block_id": "t1",
                "grow": 0.5,
                "text_style": "title",
            },
            {
                "type": "block",
                "id": "leaf-b1",
                "block_id": "b1",
                "grow": 1.5,
                "text_style": "bullet",
            },
        ],
    }


@pytest.mark.asyncio
async def test_relayout_apply_replaces_tree(client: AsyncClient) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_slides(
        client,
        headers,
        [
            {
                "title": "要点页",
                "layout_id": "bullets",
                "blocks": _bullets_blocks(),
                "layout_mode": "flex",
                "layout_tree": _simple_flex_tree(),
            }
        ],
    )
    slide = slides[0]
    # 对调叶子顺序
    swapped = {
        "type": "column",
        "id": "root",
        "gap_pt": 16,
        "children": [
            {
                "type": "block",
                "id": "leaf-b1",
                "block_id": "b1",
                "grow": 1.5,
                "text_style": "bullet",
            },
            {
                "type": "block",
                "id": "leaf-t1",
                "block_id": "t1",
                "grow": 0.5,
                "text_style": "title",
            },
        ],
    }

    response = await client.post(
        _deck_url(project["id"], slide.id, "/relayout/apply"),
        headers=headers,
        json={"revision": slide.revision, "layout_tree": swapped},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == slide.revision + 1
    tree = FlexContainer.model_validate(body["layout_tree"])
    leaves = [child.block_id for child in tree.children if isinstance(child, FlexLeaf)]
    assert leaves == ["b1", "t1"]
    # 内容不变
    assert {block["id"] for block in body["blocks"]} == {"t1", "b1"}


@pytest.mark.asyncio
async def test_relayout_propose_returns_preset_candidates(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _sign_up(client)
    project, slides = await _project_with_slides(
        client,
        headers,
        [
            {
                "title": "要点页",
                "layout_id": "bullets",
                "blocks": _bullets_blocks(),
                "layout_mode": "flex",
                "layout_tree": _simple_flex_tree(),
            }
        ],
    )
    slide = slides[0]

    class _FakeRelayout:
        async def propose(self, **_kwargs):
            return []

    monkeypatch.setattr(
        "app.api.v1.deck.layout.create_relayout_generator",
        lambda: _FakeRelayout(),
    )

    response = await client.post(
        _deck_url(project["id"], slide.id, "/relayout"),
        headers=headers,
        json={"revision": slide.revision},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == slide.revision
    assert 1 <= len(body["candidates"]) <= 3
    for candidate in body["candidates"]:
        tree = FlexContainer.model_validate(candidate["layout_tree"])
        assert set(iter_leaf_block_ids(tree)) == {"t1", "b1"}
