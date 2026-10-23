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

if __name__ == "__main__":

    try:
        data = fetch_transcript(sys.argv[1])

    except IngestionError as e :
        print(f"[{e.code}] {e.message}")
        sys.exit(1)

    print(f"{len(data)} caption pieces")

    for s in data[:5] :
        print(f"{s['start']: 7.1f}s ({s['duration']:.1f}s)  {s['text']}")

    last = data[-1]

    print(f"Video length is about {last['start'] + last['duration'] :.0f}s")
