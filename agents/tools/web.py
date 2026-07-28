from langchain.tools import ToolRuntime, tool

from agents.context import PersonaAgentContext
from rag.adaptive_graph import serialize_document
from rag.web_search import web_search_documents


@tool("web_search")
def web_search(query: str, runtime: ToolRuntime[PersonaAgentContext]) -> list[dict]:
    """Search current public web information when local knowledge is insufficient."""
    del runtime
    return [serialize_document(document) for document in web_search_documents(query, recent=True)]

