# Retrieve relevant chunks and build the grounded prompt

import sys
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

from .ingestion import OLLAMA_URL , get_embeddings , get_vectorstore

# no. of chunks to retrive per question
TOP_K = 5 
LLM_MODEL = "qwen2.5:3b"
NUM_CTX = 4096 # context window ; Ollama's default can be too smalll adn silentlt cuts teh prompt

SYSTEM_PROMPT = """You answer questions about ONE YouTube video using ONLY the transcript excerpts below.
Each excerpt starts with its timestamp in square brackets, like [12:34].

Rules:
- Use only the excerpts. If the answer is not in them, say: "I couldn't find that in the video."
- After each claim, cite the timestamp of the excerpt it came from, copied exactly, like [12:34].
- Only cite timestamps that appear in the excerpts. Never invent one.
- Keep the answer short and clear.

Excerpts:
{context}"""
ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [("system" , SYSTEM_PROMPT) ,("human" , "{question}")]
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


def get_llm() -> ChatOllama:
    # temp = 0 : we want the answer taht follows the vector db , not a creative one.
    return ChatOllama(model=LLM_MODEL , base_url=OLLAMA_URL , temperature=0, num_ctx=NUM_CTX)


def answer(video_id :str , question : str , embeddings , llm=None) ->dict :
    """Retrieve -> prompt -> LLM. Returns the answer text and the chunks it was based on."""
    docs = retrieve(video_id , question , embeddings)
    chain = ANSWER_PROMPT | (llm or get_llm())

    reply = chain.invoke({"context" :format_context(docs) , "question" : question})
    return {"answer" : reply.content , "sources" : docs}



if __name__ == "__main__":

    video_id , question = sys.argv[1] ,sys.argv[2]

    result = answer(video_id , question , get_embeddings())

    print(f"\nQ: {question}\n")
    print(f"A: {result['answer']}\n")
    print("Retrieved from: " + ", ".join(f"[{d.metadata['ts']}]" for d in result["sources"]))