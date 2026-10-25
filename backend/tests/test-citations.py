from langchain_core.documents import Document

from app.rag import verify_citations


def chunk(start, end):
    return Document(page_content="x", metadata={"start": start, "end": end, "ts": "t"})


DOCS = [chunk(64, 140), chunk(128, 204), chunk(3600, 3680)]


def test_citation_inside_a_chunk_is_snapped_to_chunk_start():
    r = verify_citations("Said here [1:10].", DOCS)
    assert r["text"] == "Said here [1:04]."
    assert r["citations"] == [64] and r["invalid"] == []


def test_invented_citation_is_removed_and_reported():
    r = verify_citations("Made up [30:00] claim.", DOCS)
    assert "[30:00]" not in r["text"]
    assert r["invalid"] == ["[30:00]"] and r["citations"] == []


def test_overlap_prefers_the_later_chunk():
    assert verify_citations("[2:10]", DOCS)["citations"] == [128]


def test_hour_format_and_no_citations():
    assert verify_citations("late [1:00:05]", DOCS)["citations"] == [3600]
    r = verify_citations("No citations here.", DOCS)
    assert r == {"text": "No citations here.", "citations": [], "invalid": []}