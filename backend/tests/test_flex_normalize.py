import json
from pathlib import Path

import pytest

from app.core.paths import REPO_ROOT
from app.domain.flex_layout import FlexContainer, FlexLeaf, FlexNode
from app.domain.flex_normalize import normalize
from app.domain.flex_solve import solve


def _leaf(node_id: str, *, grow: float = 1.0, text_style: str | None = None) -> FlexLeaf:
    return FlexLeaf(
        id=node_id,
        block_id=node_id,
        grow=grow,
        text_style=text_style,
    )


def test_snap_ratios_to_design_tokens() -> None:
    tree = FlexContainer(
        type="row",
        id="root",
        ratios=[40, 60],
        children=[_leaf("a"), _leaf("b")],
    )
    out = normalize(tree)
    assert out.ratios == [38.0, 62.0]


def test_ratio_far_from_tokens_keeps_exact_value() -> None:
    """无条件吸附会把用户拖出的比例改回去，只有靠得足够近才吸附。"""
    tree = FlexContainer(
        type="row",
        id="root",
        ratios=[55, 45],
        children=[_leaf("a"), _leaf("b")],
    )
    out = normalize(tree)
    assert out.ratios == [55.0, 45.0]
    # 幂等：再规范化一次不应再挪动
    assert normalize(out).ratios == [55.0, 45.0]


def test_title_grow_kept_when_clamp_disabled() -> None:
    """用户显式调过的标题高度不该被 TITLE_GROW_MAX 改回去。"""
    tree = FlexContainer(
        type="column",
        id="root",
        children=[
            _leaf("title", grow=1.4, text_style="title"),
            _leaf("body", grow=0.6, text_style="bullet"),
        ],
    )
    clamped = normalize(tree)
    kept = normalize(tree, clamp_title_grow=False)

    assert clamped.children[0].grow == 0.5
    assert kept.children[0].grow == pytest.approx(1.4)
    assert kept.children[1].grow == pytest.approx(0.6)
    # 幂等
    assert normalize(kept, clamp_title_grow=False).model_dump() == kept.model_dump()


def test_max_nesting_depth_flattens() -> None:
    deep = FlexContainer(
        type="column",
        id="d5",
        children=[_leaf("deep")],
    )
    d4 = FlexContainer(type="column", id="d4", children=[deep])
    d3 = FlexContainer(type="column", id="d3", children=[d4])
    d2 = FlexContainer(type="column", id="d2", children=[d3])
    root = FlexContainer(type="column", id="root", children=[d2])
    out = normalize(root)
    # root(1) -> d2(2) -> d3(3) -> d4(4) 下不应再保留容器
    node: FlexNode = out
    for _ in range(3):
        assert isinstance(node, FlexContainer)
        node = node.children[0]
    assert isinstance(node, FlexContainer)
    assert all(isinstance(child, FlexLeaf) for child in node.children)
    assert any(isinstance(child, FlexLeaf) and child.block_id == "deep" for child in node.children)


def test_flatten_keeps_empty_spacer() -> None:
    """摊平深层容器时占位块必须整体保留，否则拖拽留出的空白会被吞掉。"""
    cell = FlexContainer(
        type="column",
        id="cell-1",
        children=[
            _leaf("body"),
            FlexContainer(type="column", id="spacer-cell", children=[], gap_pt=0.0, grow=0.25),
        ],
    )
    d4 = FlexContainer(type="column", id="d4", children=[cell])
    d3 = FlexContainer(type="row", id="d3", children=[d4])
    d2 = FlexContainer(type="column", id="d2", children=[d3])
    root = FlexContainer(type="column", id="root", children=[d2])
    out = normalize(root)

    def spacer_ids(node: FlexNode) -> list[str]:
        if isinstance(node, FlexLeaf):
            return []
        found = [node.id] if node.id.startswith("spacer-") else []
        for child in node.children:
            found.extend(spacer_ids(child))
        return found

    assert spacer_ids(out) == ["spacer-cell"]


def test_flatten_keeps_resize_cell_intact() -> None:
    """摊平拆开拉伸单元格会让占位块变成同行兄弟，被拉矮的块保存后弹回满行。"""
    cell = FlexContainer(
        type="column",
        id="cell-1",
        gap_pt=0.0,
        grow=1.0,
        children=[
            _leaf("body_left", grow=1.0),
            FlexContainer(type="column", id="spacer-cell", children=[], gap_pt=0.0, grow=3.0),
        ],
    )
    points = FlexContainer(
        type="row",
        id="points",
        gap_pt=12.0,
        grow=1.5,
        ratios=[50, 50],
        children=[cell, _leaf("body_right")],
    )
    left = FlexContainer(type="column", id="left", gap_pt=12.0, children=[points])
    main = FlexContainer(
        type="row",
        id="main",
        gap_pt=16.0,
        grow=2.0,
        ratios=[62, 38],
        children=[left, _leaf("picture")],
    )
    root = FlexContainer(
        type="column",
        id="root",
        gap_pt=14.0,
        children=[_leaf("title", grow=0.45, text_style="title"), main],
    )

    out = normalize(root, clamp_title_grow=False)

    kept: FlexNode = out
    for node_id in ("main", "left", "points", "cell-1"):
        assert isinstance(kept, FlexContainer)
        kept = next(child for child in kept.children if child.id == node_id)
    assert isinstance(kept, FlexContainer)
    assert [child.id for child in kept.children] == ["body_left", "spacer-cell"]

    def height(tree: FlexContainer, block_id: str) -> float:
        return next(p.rect.h for p in solve(tree) if p.block_id == block_id)

    assert height(out, "body_left") == pytest.approx(height(root, "body_left"), abs=1e-2)
    # 重标一轮会有 1ULP 级抖动，几何上必须稳定
    settled = normalize(out, clamp_title_grow=False)
    assert height(settled, "body_left") == pytest.approx(height(out, "body_left"), abs=1e-2)


def test_max_four_children_per_row_wraps_overflow() -> None:
    tree = FlexContainer(
        type="row",
        id="root",
        ratios=[20, 20, 20, 20, 20],
        children=[_leaf(f"c{i}") for i in range(5)],
    )
    out = normalize(tree)
    assert len(out.children) == 4
    assert isinstance(out.children[-1], FlexContainer)
    overflow = out.children[-1]
    assert isinstance(overflow, FlexContainer)
    assert overflow.type == "column"
    assert len(overflow.children) == 2


def test_clamp_grow_range() -> None:
    tree = FlexContainer(
        type="column",
        id="root",
        children=[_leaf("tiny", grow=0.01), _leaf("huge", grow=99.0)],
    )
    out = normalize(tree)
    grows = [child.grow for child in out.children]
    assert all(0.25 <= g <= 4.0 for g in grows)


@pytest.mark.parametrize("spacer_grow", [0.0, 0.001])
def test_spacer_grow_keeps_near_zero(spacer_grow: float) -> None:
    """上下拉伸把 spacer 收到 0 后，normalize 不得抬到 GROW_MIN。"""
    tree = FlexContainer(
        type="column",
        id="root",
        gap_pt=0.0,
        children=[
            FlexContainer(
                type="column",
                id="spacer-top",
                children=[],
                gap_pt=0.0,
                grow=spacer_grow,
            ),
            _leaf("body", grow=1.0),
        ],
    )
    out = normalize(tree)
    spacer = out.children[0]
    assert isinstance(spacer, FlexContainer)
    assert spacer.grow == pytest.approx(spacer_grow)
    assert normalize(out).children[0].grow == pytest.approx(spacer_grow)


def test_title_like_grow_clamped() -> None:
    tree = FlexContainer(
        type="column",
        id="root",
        children=[
            _leaf("title", grow=2.0, text_style="title"),
            _leaf("body", grow=2.0, text_style="body"),
        ],
    )
    out = normalize(tree)
    title = out.children[0]
    assert isinstance(title, FlexLeaf)
    assert title.grow <= 0.5


def test_ratios_length_mismatch_fills_equal() -> None:
    tree = FlexContainer(
        type="row",
        id="root",
        ratios=[70],
        children=[_leaf("a"), _leaf("b"), _leaf("c")],
    )
    out = normalize(tree)
    assert out.ratios is not None
    assert len(out.ratios) == 3
    assert out.ratios == [100.0 / 3, 100.0 / 3, 100.0 / 3]


def test_normalize_is_idempotent() -> None:
    tree = FlexContainer(
        type="row",
        id="root",
        ratios=[41, 59],
        children=[
            _leaf("img", grow=1.2),
            FlexContainer(
                type="column",
                id="col",
                children=[
                    _leaf("title", grow=3.0, text_style="subtitle"),
                    _leaf("body", grow=0.1),
                    _leaf("note", grow=8.0),
                ],
            ),
        ],
    )
    once = normalize(tree)
    twice = normalize(once)
    assert once.model_dump() == twice.model_dump()


def test_spacer_grow_stays_locked_and_content_weights_preserved() -> None:
    tree = FlexContainer(
        type="column",
        id="root",
        children=[
            _leaf("a", grow=1.0),
            _leaf("b", grow=2.0),
            FlexContainer(type="column", id="spacer-gap", children=[], gap_pt=0.0, grow=1.5),
        ],
    )
    out = normalize(tree)
    assert [child.grow for child in out.children] == [1.0, 2.0, 1.5]
    spacer = out.children[2]
    assert isinstance(spacer, FlexContainer)
    assert spacer.id.startswith("spacer-")


def test_grow_cases_match_shared_fixture() -> None:
    """同一份 fixture 前端 flexNormalize.ts 也会断言：两端不一致会让拖拽结果保存后被改写。"""
    path = Path(REPO_ROOT) / "shared" / "flex-fixtures" / "normalize-grow-cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    assert cases

    def collect(node: FlexNode, out: dict[str, float]) -> dict[str, float]:
        out[node.id] = node.grow
        if isinstance(node, FlexContainer):
            for child in node.children:
                collect(child, out)
        return out

    for case in cases:
        tree = FlexContainer.model_validate(case["tree"])
        once = normalize(tree, clamp_title_grow=False)
        assert collect(once, {}) == case["expected_grows"], case["name"]
        twice = normalize(once, clamp_title_grow=False)
        assert twice.model_dump() == once.model_dump(), case["name"]


def test_golden_fixture_normalizes_stably() -> None:
    path = Path(REPO_ROOT) / "shared" / "flex-presets" / "golden-image-left.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    tree = FlexContainer.model_validate(payload["tree"])
    once = normalize(tree)
    twice = normalize(once)
    assert once.model_dump() == twice.model_dump()
    assert once.ratios == [38.0, 62.0]
