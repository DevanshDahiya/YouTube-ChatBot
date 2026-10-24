# Retrieve relevant chunks and build the grounded prompt
import re
import sys
import time 

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

from .ingestion import OLLAMA_URL ,fmt_ts, get_embeddings , get_vectorstore

# no. of chunks to retrive per question
TOP_K = 5 
LLM_MODEL = "qwen2.5:3b"
NUM_CTX = 4096 # context window ; Ollama's default can be too smalll adn silentlt cuts teh prompt

SYSTEM_PROMPT = """You answer questions about ONE YouTube video using ONLY the transcript excerpts below.
Each excerpt starts with its timestamp in square brackets, like [12:34].

Rules:
- Use only the excerpts. If the answer is not in them, say: "I couldn't find that in the video."
- End EVERY sentence of your answer with the timestamp of the excerpt it came from, copied exactly, like [12:34].
- Only cite timestamps that appear in the excerpts. Never invent one.
- Keep the answer short: 2 to 4 sentences.

Example of the format (different topic):
Excerpts:
[2:08] a bakery sells bread and also sells cakes on weekends
[3:12] the owner opens the shop at six in the morning
Question: When does the bakery open?
Answer: The owner opens the shop at six in the morning [3:12].

Excerpts:
{context}"""

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            "{question}\n\n"
            "(Answer using only the excerpts, and end every sentence with a timestamp like [12:34].)",
        ),
    ]
)

def retrieve(video_id : str , question: str , embeddings , k : int = TOP_K) -> list[Document] :
    """Find the k chunks closest in meaning to the question, then put them in video order."""
    store = get_vectorstore(video_id , embeddings)
    docs = store.similarity_search(question , k = k)
    # sort the dcos based on start time , so that the model has a continuous idea of the video's content
    return sorted(docs , key = lambda d : d.metadata["start"])

def format_context(docs : list[Document]) ->str :
    """Put the timestamp in front of each chunk's text , exactly as the model should cite it."""
    return "\n\n".join(f"[{d.metadata['ts']}] {d.page_content}" for d in docs)


# regex to find the timestamp - # [M:SS] or [H:MM:SS]
_CITE_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")

# convert the time to seconds
def _to_seconds(m : re.Match) -> int :
    a,b , c = m.group(1), m.group(2) , m.group(3)

    return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a)* 60 + int(b) 

def verify_citations(text: str , docs : list[Document]) -> dict:
    """Keep only citations that point inside a retrieved chunk; snap them to that chunk's start.

    - cited time inside a retrieved chunk -> rewritten to the chunk's start (a real, seekable moment)
    - cited time in NO retrieved chunk    -> removed from the text and reported in `invalid`
    """

    valid : set[int] = set()
    invalid : list[str] = []

    def fix(m: re.Match) -> str:
        t = _to_seconds(m)
        containing = [d.metadata["start"] for d in docs if d.metadata["start"] <= t <= d.metadata["end"]]
        if not containing:
            invalid.append(m.group(0))
            return ""
        start = max(containing)  # in an overlap, prefer the chunk that started latest
        valid.add(start)
        return f"[{fmt_ts(start)}]"

    cleaned = _CITE_RE.sub(fix , text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).replace(" .", ".").replace(" ,", ",").strip()
    return {"text": cleaned, "citations": sorted(valid), "invalid": invalid}






def get_llm() -> ChatOllama:
    # temp = 0 : we want the answer taht follows the vector db , not a creative one.
    return ChatOllama(model=LLM_MODEL , base_url=OLLAMA_URL , temperature=0, num_ctx=NUM_CTX)


def answer(video_id :str , question : str , embeddings , llm=None) ->dict :
    """Retrieve -> prompt -> LLM. Returns the answer text and the chunks it was based on."""
    docs = retrieve(video_id , question , embeddings)
    chain = ANSWER_PROMPT | (llm or get_llm())

    reply = chain.invoke({"context" :format_context(docs) , "question" : question})

    checked = verify_citations(reply.content , docs)
    return {
        "answer" :checked["text"],
        "citations" : checked["citations"], # seconds the player can seek to 
        "invalid_citations" : checked["invalid"], # timestamps the model made up (removed from the answer)
        "sources" : docs,
    }

# use the stream in place of invoke , to take the feel of streaming the text, 
def stream_answer(video_id :str , question :str , embeddings , llm=None):
    """Same as answer(), but yields events as they happen:

    {"type": "sources", "sources": [...]}   once, before any text
    {"type": "token",   "text": "..."}      many times, raw model text
    {"type": "final",   "answer", "citations", "invalid_citations"}   once, citations verified
    """
    docs = retrieve(video_id , question , embeddings)

    yield{
        "type":"sources",
        "sources":[
            {"start" :d.metadata["start"], "ts" : d.metadata["ts"], "snippet": d.page_content[:160]} for d in docs
        ],
    }

    chain = ANSWER_PROMPT  | (llm or get_llm())

    parts : list[str] = []

    for chunk in chain.stream({"context" :format_context(docs) , "question" : question}):
        if chunk.content:
            parts.append(chunk.content)
            yield {"type": "token" , "text" :chunk.content}

    checked = verify_citations("".join(parts), docs)
    yield{
        "type" :"final",
        "answer": checked["text"],
        "citations" : checked["citations"],
        "invalid_citations" : checked["invalid"],
    }


if __name__ == "__main__":

    video_id , question = sys.argv[1] ,sys.argv[2]

    t0 = time.perf_counter()
    first_token = None

    result = answer(video_id , question , get_embeddings())

    print(f"\nQ: {question}\n")
    print("A: " , end="" , flush=True)

    for event in stream_answer(video_id , question , get_embeddings()):
        if event["type"] == "token" :
            if first_token is None :
                first_token = time.perf_counter() - t0
            print(event["text"], end ="" , flush= True) #appers word by word

        elif event["type"] == "final" :
            print("\n\n--- after citation check ---")
            print(event["answer"])
            print("Valid citations (seconds): ", event["citations"])
            print("Removed (not in retrieved chunks): ", event["invalid_citations"])

    total = time.perf_counter() - t0
    print(f"\nfirst token after {first_token:.1f}s, finished after {total:.1f}s")

