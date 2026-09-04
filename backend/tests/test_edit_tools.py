import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.domain.content import TextBlock
from app.domain.flex_layout import FlexContainer, FlexLeaf
from app.llm.base import AiEditHistoryTurn, SlideEditBlockInput, SlideEditInput
from app.llm.edit_tools import EditSession, build_edit_tools
from app.llm.slide_edit import DeepSeekSlideEditGenerator


def _text(block_id: str, text: str, *, locked: bool = False) -> TextBlock:
    return TextBlock(id=block_id, slot_id=block_id, text=text, locked=locked)


def _tree(*block_ids: str) -> FlexContainer:
    return FlexContainer(
        type="column",
        id="root",
        children=[FlexLeaf(id=f"leaf-{item}", block_id=item, grow=1) for item in block_ids],
    )


def _tool_map(session: EditSession) -> dict[str, object]:
    return {tool.name: tool for tool in build_edit_tools(session)}


@pytest.mark.asyncio
async def test_fixed_session_has_no_structure_tools() -> None:
    session = EditSession(blocks=[_text("a", "标题")], tree=None, layout_mode="fixed")
    names = set(_tool_map(session))
    assert "replace_text" in names
    assert "add_block" not in names
    assert "delete_block" not in names
    assert "change_type" not in names


@pytest.mark.asyncio
async def test_flex_can_add_delete_and_change_type() -> None:
    session = EditSession(
        blocks=[_text("a", "标题"), _text("b", "导语")],
        tree=_tree("a", "b"),
        layout_mode="flex",
    )
    tools = _tool_map(session)

    added = await tools["add_block"].ainvoke(
        {"after_block_id": "a", "block_type": "text", "text": "新段"}
    )
    assert added.startswith("已新增")
    assert len(session.blocks) == 3

    new_id = next(block.id for block in session.blocks if block.id not in {"a", "b"})
    changed = await tools["change_type"].ainvoke(
        {"block_id": new_id, "new_type": "bullets", "items": ["一点"]}
    )
    assert "bullets" in changed
    updated = session.by_id(new_id)
    assert updated is not None
    assert updated.type == "bullets"

    deleted = await tools["delete_block"].ainvoke({"block_id": new_id})
    assert deleted.startswith("已删除")
    assert [block.id for block in session.blocks] == ["a", "b"]


@pytest.mark.asyncio
async def test_tools_reject_locked_and_last_block() -> None:
    locked_session = EditSession(
        blocks=[_text("a", "锁定", locked=True), _text("b", "可删")],
        tree=_tree("a", "b"),
        layout_mode="flex",
    )
    locked_tools = _tool_map(locked_session)

    locked = await locked_tools["delete_block"].ainvoke({"block_id": "a"})
    assert "人工修改" in locked
    assert locked_session.by_id("a") is not None

    replace_locked = await locked_tools["replace_text"].ainvoke({"block_id": "a", "text": "覆盖"})
    assert "人工修改" in replace_locked

    session = EditSession(
        blocks=[_text("a", "一段"), _text("b", "二段")],
        tree=_tree("a", "b"),
        layout_mode="flex",
    )
    tools = _tool_map(session)
    await tools["delete_block"].ainvoke({"block_id": "b"})
    last = await tools["delete_block"].ainvoke({"block_id": "a"})
    assert "至少保留" in last
    assert len(session.blocks) == 1


class RecordingModel:
    def __init__(self) -> None:
        self.seen: list = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.seen.append(list(messages))
        return AIMessage(content="无需修改")


@pytest.mark.asyncio
async def test_edit_generator_puts_history_in_messages() -> None:
    model = RecordingModel()
    generator = DeepSeekSlideEditGenerator(model=model, api_key="test-key")
    blocks = [_text("t1", "原标题")]
    result = await generator.generate(
        SlideEditInput(
            deck_title="演示",
            tone="专业",
            page_title="要点页",
            layout_id="bullets",
            instruction="再短一点",
            history=[AiEditHistoryTurn(instruction="把标题改商务", note="改了 1 处")],
            blocks=[
                SlideEditBlockInput(
                    block_id="t1",
                    slot_id="t1",
                    type="text",
                    text="原标题",
                )
            ],
            original_blocks=blocks,
        )
    )
    assert result.operations == []
    first = model.seen[0]
    texts = [item.content for item in first if isinstance(item, HumanMessage)]
    assert "把标题改商务" in texts
    assert any("再短一点" in text for text in texts)
