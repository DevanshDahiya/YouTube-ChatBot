from app.ingestion import chunk_transcript, fmt_ts

def snippets(n=300 , step = 4.0):
    return [{"text" : f"s{i}" , "start" : i*step , "duration": step} for i in range(n)]

def test_chunks_start_at_real_caption_times_and_move_forward():
    chunks = chunk_transcript(snippets() , window=75 , overlap=15)
    assert chunks[0]["start"] == 0 
    starts = [c["start"] for c in chunks]

    assert starts == sorted(set(starts))  # strictly increasing: no stuck loop, no duplicates
    assert all(s % 4 == 0 for s in starts)  # always a real caption start


def test_neighbouring_chunks_overlap():
    chunks = chunk_transcript(snippets(), window=75, overlap=15)
    for a, b in zip(chunks, chunks[1:]):
        assert a["start"] < b["start"] < a["end"]


def test_no_caption_is_dropped():
    text = " ".join(c["text"] for c in chunk_transcript(snippets()))
    assert all(f"s{i}" in text.split() for i in range(300))


def test_edge_cases():
    assert chunk_transcript([]) == []
    one_long = [{"text": "x", "start": 5.0, "duration": 500.0}]
    assert len(chunk_transcript(one_long)) == 1


def test_fmt_ts():
    assert fmt_ts(65) == "1:05"
    assert fmt_ts(3725) == "1:02:05"