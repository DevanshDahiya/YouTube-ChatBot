"""Step 1: validate a video ID and fetch its transcript from YouTube."""
import re 
import sys

from youtube_transcript_api import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi
)

LANGUAGES=["en", "en-US", "en-GB","hi"]
WINDOW_SECONDS = 75
OVERLAP_SECONDS = 15



_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class IngestionError(Exception):
    """Carries a stable code so the API and extension can show a friendly message"""
    def __init__(self , code :str , message: str):
        super().__init__(message)
        self.code = code
        self.message = message

# Function is to check teh video id is valid or not 
def validate_video_id(video_id: str) -> str :
    if not _VIDEO_ID_RE.match(video_id or ""):
        raise IngestionError("invalid_video_id","Video ID must be 11 characters (letters, digits, - or _).")
    return video_id

# Now fetching the transcript of the video by using the validated  vide_id
def fetch_transcript(video_id : str)-> list[dict] :
    """Return [{"text" , "start" ,"duration"}, .... ] for the video"""
    # check the video_id
    validate_video_id(video_id)
    try :
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=LANGUAGES)

    except (TranscriptsDisabled , NoTranscriptFound):
        raise IngestionError("no_captions", "This video has no usable captions.")

    except VideoUnavailable:
        raise IngestionError("video_unavailable" ,"This video is unavailable.")

    except (RequestBlocked , IpBlocked):
        raise IngestionError("blocked" , "YouTube blocked teh request from this network/IP")


    snippets = []

    for s in fetched:
        if s.text.strip():
            snippet = {
                "text": " ".join(s.text.split()),
                "start": float(s.start),
                "duration": float(s.duration)
            }
            snippets.append(snippet)

    if not snippets :
        raise IngestionError("no_captions" , "Transcript was empty.")

    return snippets


def fmt_ts(seconds : float) -> str :
    """754 -> '12:34', 3725 -> 1:02:05 """

    s = int(seconds)
    h, rem = divmod(s , 3600)
    m, sec = divmod(rem , 60)
    return f"{h} : {m:02d} : {sec:02d}" if h else f"{m}:{sec:02d}"

def chunk_transcript(snippets: list[dict] , window: int = WINDOW_SECONDS , overlap : int = OVERLAP_SECONDS) -> list[dict] :
    """Group caption pieces into ~`window`-second chunks; neighbours share ~`overlap` seconds.

    Each chunk: {"text", "start", "end", "chunk_index"}.
    `start` is the real start time of the chunk's first caption, so it is safe to cite and seek to.
    """
    chunks : list[dict] = []
    i , n = 0 , len(snippets) 
    while i < n :
        start = snippets[i]["start"]
        j = i 

        while j < n and snippets[j]["start"] < start + window:
            j += 1 
        j = max(j , i+1) # always take at least one piece
        group = snippets[i:j]
        end = group[-1]["start"] + group[-1]["duration"]
        chunks.append(
            {
                "text" : " ".join(g["text"] for g in group),
                "start" :start,
                "end" :end ,
                "chunk_index" :len(chunks),
            }
        )

        if j >= n :
            break

        k = j 
        while k-1 > i and snippets[k-1]["start"] >= end - overlap:
            k -= 1

        i = k # k > i , so we always move forward

    return chunks


if __name__ == "__main__":

    try:
        data = fetch_transcript(sys.argv[1])

    except IngestionError as e :
        print(f"[{e.code}] {e.message}")
        sys.exit(1)

    print(f"{len(data)} caption pieces")

    chunks = chunk_transcript(data)

    print(f"-> {len(chunks)} chunks (window={WINDOW_SECONDS}s , overlap={OVERLAP_SECONDS}s)\n")

    for c in chunks[:3]:
        print(f"[{fmt_ts(c['start'])} - {fmt_ts(c['end'])}]  {len(c['text'].split())} words")

        print(f"  {c['text'][:150]}...\n")
