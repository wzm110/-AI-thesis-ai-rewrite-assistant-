from __future__ import annotations

from paper_rewrite.core import RewriteJob, merge_job_output, merge_short_chunks, split_text_to_max_len


def test_split_text_to_max_len_smoke() -> None:
    s = "a" * 10
    parts = split_text_to_max_len(s, max_len=250)
    assert parts == [s]


def test_split_text_to_max_len_constraints() -> None:
    s = ("a" * 600)  # no whitespace/punctuation; recursion should still respect max_len
    parts = split_text_to_max_len(s, max_len=250)
    assert parts, "should not be empty"
    assert all(len(p) <= 250 for p in parts)
    assert sum(len(p) for p in parts) == len(s.strip())


def test_merge_short_chunks_merges_when_possible() -> None:
    chunks = [
        ("sec", "a" * 5, 0),
        ("sec", "b" * 5, 1),
        ("sec", "c" * 5, 2),
    ]
    merged = merge_short_chunks(chunks, short_len=15, max_len=20)
    assert len(merged) == 1
    _sec, txt, _pid = merged[0]
    assert len(txt) <= 20
    assert "a" in txt and "b" in txt and "c" in txt


def test_merge_short_chunks_respects_max_len() -> None:
    chunks = [
        ("sec", "a" * 10, 0),
        ("sec", "b" * 10, 1),
    ]
    merged = merge_short_chunks(chunks, short_len=15, max_len=15)
    # 10 + 1 + 10 > 15, so they must not merge into a single chunk.
    assert len(merged) == 2
    assert all(len(txt) <= 15 for _sec, txt, _pid in merged)


def test_merge_job_output_orders_and_marks_errors() -> None:
    job = RewriteJob(
        job_id="job_x",
        created_at=0.0,
        total=3,
        tasks=[],
        results={0: "A", 2: "C"},
        errors={1: "err_msg"},
        finished=True,
    )
    out = merge_job_output(job)
    parts = out.split("\n\n")
    assert parts == ["A", "[任务失败]err_msg", "C"]

