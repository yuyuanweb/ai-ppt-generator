from app.domain.content import BulletsBlock, Slide, TextBlock
from app.domain.content_density import (
    DEFAULT_CONTENT_DENSITY,
    contains_empty_phrase,
    density_prompt_block,
    effective_block_targets,
    get_profile,
    normalize_density,
)
from app.domain.quality import check_empty_phrases, check_thin_content, is_repair_worthy
from app.domain.validation import StructureIssue


def test_normalize_density_defaults() -> None:
    assert normalize_density(None) == DEFAULT_CONTENT_DENSITY
    assert normalize_density("weird") == "medium"
    assert normalize_density("detailed") == "detailed"


def test_profiles_have_rising_block_targets() -> None:
    concise = get_profile("concise")
    medium = get_profile("medium")
    detailed = get_profile("detailed")
    assert concise.min_blocks < medium.min_blocks <= detailed.min_blocks
    assert concise.target_bullets[0] <= medium.target_bullets[0] <= detailed.target_bullets[0]


def test_cover_role_lowers_block_targets() -> None:
    assert effective_block_targets("detailed", "cover") == (2, 3)
    assert effective_block_targets("medium", "content")[0] >= 4


def test_density_prompt_mentions_multi_block() -> None:
    text = density_prompt_block("medium", "content")
    assert "文字量档位" in text
    assert "禁止仅输出" in text
    assert "medium" in text


def test_empty_phrase_detection() -> None:
    assert contains_empty_phrase("本页介绍增长策略")
    assert contains_empty_phrase("lorem ipsum dolor")
    assert not contains_empty_phrase("交付周期从 6 周缩短到 3 周")


def test_thin_and_empty_quality_codes() -> None:
    slide = Slide(
        id="s1",
        layout_id="bullets",
        layout_mode="flex",
        blocks=[
            TextBlock(id="t", slot_id="title", text="本页介绍现状"),
            BulletsBlock(id="b", slot_id="body", items=["短", "也短"]),
        ],
    )
    empty = check_empty_phrases(slide)
    thin = check_thin_content(slide, content_density="medium", page_role="content")
    assert empty and empty[0].code == "empty_phrase"
    assert any(issue.code == "thin_content" for issue in thin)


def test_fixed_layout_skips_block_count_thin_check() -> None:
    slide = Slide(
        id="s1",
        layout_id="bullets",
        layout_mode="fixed",
        blocks=[
            TextBlock(id="t", slot_id="title", text="现状与问题"),
            BulletsBlock(
                id="b",
                slot_id="body",
                items=[
                    "交付周期从六周缩短到三周，瓶颈在评审排队",
                    "重复建设占比过高，跨团队接口缺少统一契约",
                    "线上故障平均恢复时间仍超过四小时，需专人值班",
                ],
            ),
        ],
    )
    issues = check_thin_content(slide, content_density="medium", page_role="content")
    assert issues == []


def test_repair_worthy_skips_overflow_capacity() -> None:
    assert (
        is_repair_worthy(
            StructureIssue(
                severity="warning",
                slide_id="s",
                slot_id="body",
                message="溢出",
                code="overflow",
            )
        )
        is False
    )
    assert is_repair_worthy(
        StructureIssue(
            severity="warning",
            slide_id="s",
            slot_id=None,
            message="过瘦",
            code="thin_content",
        )
    )
    assert is_repair_worthy(
        StructureIssue(
            severity="error",
            slide_id="s",
            slot_id=None,
            message="缺槽",
            code=None,
        )
    )
