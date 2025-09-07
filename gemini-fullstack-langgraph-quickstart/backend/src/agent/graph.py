import os
import json

from agent.tools_and_schemas import SearchQueryList, Reflection
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.types import Send
from langgraph.graph import StateGraph
from langgraph.graph import START, END
from langchain_core.runnables import RunnableConfig
from google.genai import Client

from agent.state import (
    OverallState,
    QueryGenerationState,
    ReflectionState,
    WebSearchState,
)
from agent.configuration import Configuration
from agent.prompts import (
    get_current_date,
    query_writer_instructions,
    web_searcher_instructions,
    reflection_instructions,
    answer_instructions,
)
from langchain_google_genai import ChatGoogleGenerativeAI
from agent.utils import (
    get_citations,
    get_research_topic,
    insert_citation_markers,
    resolve_urls,
)

load_dotenv()

def _get_gemini_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    return key

def _genai_generate_content(model, contents, config):
    """
    Robust generate_content via google.genai Client with DEEPRESEARCH_AI_BASE_URL normalization.
    Tries provided base as-is; if it ends with /v1 or /v1beta also tries trimmed variant;
    else also tries appending /v1beta.
    """
    api_key = _get_gemini_api_key()
    base = os.getenv("DEEPRESEARCH_AI_BASE_URL", "").strip()
    attempts = []
    if base:
        b = base.rstrip("/")
        attempts.append(b)
        if b.endswith("/v1"):
            attempts.append(b[:-3].rstrip("/"))
        elif b.endswith("/v1beta"):
            attempts.append(b[:-7].rstrip("/"))
        else:
            attempts.append(b + "/v1beta")
    else:
        attempts.append(None)
    last_exc = None
    for ab in attempts:
        try:
            client = Client(api_key=api_key, base_url=ab) if ab else Client(api_key=api_key)
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:
            last_exc = e
            continue
    raise last_exc

# Note: Defer external client creation to runtime inside nodes to avoid import-time failures


# Nodes
def generate_query(state: OverallState, config: RunnableConfig) -> QueryGenerationState:
    """LangGraph node that generates search queries based on the User's question.

    Uses Gemini 2.0 Flash to create an optimized search queries for web research based on
    the User's question.

    Args:
        state: Current graph state containing the User's question
        config: Configuration for the runnable, including LLM provider settings

    Returns:
        Dictionary with state update, including search_query key containing the generated queries
    """
    configurable = Configuration.from_runnable_config(config)

    # check for custom initial search query count
    if state.get("initial_search_query_count") is None:
        state["initial_search_query_count"] = configurable.number_of_initial_queries

    # Format the prompt
    current_date = get_current_date()
    formatted_prompt = query_writer_instructions.format(
        current_date=current_date,
        research_topic=get_research_topic(state["messages"]),
        number_queries=state["initial_search_query_count"],
    )

    # If a custom Gemini-compatible base URL is provided, use google.genai Client to ensure routing via the relay.
    base_url = os.getenv("DEEPRESEARCH_AI_BASE_URL", "").strip()
    if base_url:
        client = Client(api_key=_get_gemini_api_key(), base_url=base_url)
        json_prompt = (
            formatted_prompt
            + "\n\n仅返回一个JSON对象，格式严格为：{\"query\": [\"...\"]}。不要输出任何额外文本。"
        )
        response = client.models.generate_content(
            model=configurable.query_generator_model,
            contents=json_prompt,
            config={"temperature": 1.0},
        )
        text = getattr(response, "text", "") or ""
        queries: list[str] = []
        try:
            start = text.find("{")
            end = text.rfind("}")
            payload = text[start : end + 1] if start != -1 and end != -1 and end > start else text
            data = json.loads(payload)
            q = data.get("query") or data.get("queries") or []
            if isinstance(q, list):
                queries = [str(item) for item in q]
            elif q:
                queries = [str(q)]
        except Exception:
            # Fallback: split lines
            queries = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]

        return {"search_query": queries or []}
    else:
        llm = ChatGoogleGenerativeAI(
            model=configurable.query_generator_model,
            temperature=1.0,
            max_retries=2,
            api_key=_get_gemini_api_key(),
        )
        structured_llm = llm.with_structured_output(SearchQueryList)
        result = structured_llm.invoke(formatted_prompt)
        return {"search_query": result.query}


def continue_to_web_research(state: QueryGenerationState):
    """LangGraph node that sends the search queries to the web research node.

    This is used to spawn n number of web research nodes, one for each search query.
    """
    return [
        Send("web_research", {"search_query": search_query, "id": int(idx)})
        for idx, search_query in enumerate(state["search_query"])
    ]


def web_research(state: WebSearchState, config: RunnableConfig) -> OverallState:
    """LangGraph node that performs web research using the native Google Search API tool.

    Executes a web search using the native Google Search API tool in combination with Gemini 2.0 Flash.

    Args:
        state: Current graph state containing the search query and research loop count
        config: Configuration for the runnable, including search API settings

    Returns:
        Dictionary with state update, including sources_gathered, research_loop_count, and web_research_results
    """
    # Configure
    configurable = Configuration.from_runnable_config(config)
    formatted_prompt = web_searcher_instructions.format(
        current_date=get_current_date(),
        research_topic=state["search_query"],
    )

    # Uses the google genai client as the langchain client doesn't return grounding metadata
    response = _genai_generate_content(
        configurable.query_generator_model,
        formatted_prompt,
        {"tools": [{"google_search": {}}], "temperature": 0},
    )

    # Resolve grounding safely (keys may be absent if the account/region disallows google_search tool)
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks
        resolved_urls = resolve_urls(chunks, state["id"])
    except Exception:
        resolved_urls = {}

    # Gets the citations and adds them to the generated text (gracefully handle no-grounding case)
    citations = get_citations(response, resolved_urls)
    base_text = getattr(response, "text", "") or ""
    modified_text = insert_citation_markers(base_text, citations) if citations else base_text
    sources_gathered = [item for citation in citations for item in citation.get("segments", [])]

    return {
        "sources_gathered": sources_gathered,
        "search_query": [state["search_query"]],
        "web_research_result": [modified_text],
    }


def reflection(state: OverallState, config: RunnableConfig) -> ReflectionState:
    """LangGraph node that identifies knowledge gaps and generates potential follow-up queries.

    Analyzes the current summary to identify areas for further research and generates
    potential follow-up queries. Uses structured output to extract
    the follow-up query in JSON format.

    Args:
        state: Current graph state containing the running summary and research topic
        config: Configuration for the runnable, including LLM provider settings

    Returns:
        Dictionary with state update, including search_query key containing the generated follow-up query
    """
    configurable = Configuration.from_runnable_config(config)
    # Increment the research loop count and get the reasoning model
    state["research_loop_count"] = state.get("research_loop_count", 0) + 1
    reasoning_model = state.get("reasoning_model", configurable.reflection_model)

    # Format the prompt
    current_date = get_current_date()
    formatted_prompt = reflection_instructions.format(
        current_date=current_date,
        research_topic=get_research_topic(state["messages"]),
        summaries="\n\n---\n\n".join(state["web_research_result"]),
    )
    # init Reasoning Model
    base_url = os.getenv("DEEPRESEARCH_AI_BASE_URL", "").strip()
    if base_url:
        json_prompt = (
            formatted_prompt
            + "\n\n仅返回一个JSON对象，严格包含字段："
            + "{\"is_sufficient\": <true|false>, \"knowledge_gap\": \"...\", \"follow_up_queries\": [\"...\"]}。"
            + "不要输出任何额外文本。"
        )
        response = _genai_generate_content(
            reasoning_model,
            json_prompt,
            {"temperature": 1.0},
        )
        text = getattr(response, "text", "") or ""
        is_sufficient = False
        knowledge_gap = ""
        follow_up_queries: list[str] = []
        try:
            start = text.find("{")
            end = text.rfind("}")
            payload = text[start : end + 1] if start != -1 and end != -1 and end > start else text
            data = json.loads(payload)
            is_sufficient = bool(data.get("is_sufficient", False))
            knowledge_gap = str(data.get("knowledge_gap", "") or "")
            fu = data.get("follow_up_queries") or []
            follow_up_queries = [str(x) for x in fu] if isinstance(fu, list) else ([str(fu)] if fu else [])
        except Exception:
            # If parsing fails, keep conservative defaults (force another loop)
            is_sufficient = False
            follow_up_queries = []

        return {
            "is_sufficient": is_sufficient,
            "knowledge_gap": knowledge_gap,
            "follow_up_queries": follow_up_queries,
            "research_loop_count": state["research_loop_count"],
            "number_of_ran_queries": len(state["search_query"]),
        }
    else:
        llm = ChatGoogleGenerativeAI(
            model=reasoning_model,
            temperature=1.0,
            max_retries=2,
            api_key=_get_gemini_api_key(),
        )
        result = llm.with_structured_output(Reflection).invoke(formatted_prompt)
        return {
            "is_sufficient": result.is_sufficient,
            "knowledge_gap": result.knowledge_gap,
            "follow_up_queries": result.follow_up_queries,
            "research_loop_count": state["research_loop_count"],
            "number_of_ran_queries": len(state["search_query"]),
        }


def evaluate_research(
    state: ReflectionState,
    config: RunnableConfig,
) -> OverallState:
    """LangGraph routing function that determines the next step in the research flow.

    Controls the research loop by deciding whether to continue gathering information
    or to finalize the summary based on the configured maximum number of research loops.

    Args:
        state: Current graph state containing the research loop count
        config: Configuration for the runnable, including max_research_loops setting

    Returns:
        String literal indicating the next node to visit ("web_research" or "finalize_summary")
    """
    configurable = Configuration.from_runnable_config(config)
    max_research_loops = (
        state.get("max_research_loops")
        if state.get("max_research_loops") is not None
        else configurable.max_research_loops
    )
    if state["is_sufficient"] or state["research_loop_count"] >= max_research_loops:
        return "finalize_answer"
    else:
        return [
            Send(
                "web_research",
                {
                    "search_query": follow_up_query,
                    "id": state["number_of_ran_queries"] + int(idx),
                },
            )
            for idx, follow_up_query in enumerate(state["follow_up_queries"])
        ]


def finalize_answer(state: OverallState, config: RunnableConfig):
    """LangGraph node that finalizes the research summary.

    Prepares the final output by deduplicating and formatting sources, then
    combining them with the running summary to create a well-structured
    research report with proper citations.

    Args:
        state: Current graph state containing the running summary and sources gathered

    Returns:
        Dictionary with state update, including running_summary key containing the formatted final summary with sources
    """
    configurable = Configuration.from_runnable_config(config)
    reasoning_model = state.get("reasoning_model") or configurable.answer_model

    # Format the prompt
    current_date = get_current_date()
    formatted_prompt = answer_instructions.format(
        current_date=current_date,
        research_topic=get_research_topic(state["messages"]),
        summaries="\n---\n\n".join(state["web_research_result"]),
    )

    # init Reasoning Model, default to Gemini 2.5 Flash
    base_url = os.getenv("DEEPRESEARCH_AI_BASE_URL", "").strip()
    if base_url:
        response = _genai_generate_content(
            reasoning_model,
            formatted_prompt,
            {"temperature": 0},
        )
        result_text = getattr(response, "text", "") or ""
        # Wrap into AIMessage to keep downstream logic unchanged
        result = AIMessage(content=result_text)
    else:
        llm = ChatGoogleGenerativeAI(
            model=reasoning_model,
            temperature=0,
            max_retries=2,
            api_key=_get_gemini_api_key(),
        )
        result = llm.invoke(formatted_prompt)

    # Replace the short urls with the original urls and add all used urls to the sources_gathered
    unique_sources = []
    for source in state["sources_gathered"]:
        if source["short_url"] in result.content:
            result.content = result.content.replace(
                source["short_url"], source["value"]
            )
            unique_sources.append(source)

    return {
        "messages": [AIMessage(content=result.content)],
        "sources_gathered": unique_sources,
    }


# Create our Agent Graph
builder = StateGraph(OverallState, config_schema=Configuration)

# Define the nodes we will cycle between
builder.add_node("generate_query", generate_query)
builder.add_node("web_research", web_research)
builder.add_node("reflection", reflection)
builder.add_node("finalize_answer", finalize_answer)

# Set the entrypoint as `generate_query`
# This means that this node is the first one called
builder.add_edge(START, "generate_query")
# Add conditional edge to continue with search queries in a parallel branch
builder.add_conditional_edges(
    "generate_query", continue_to_web_research, ["web_research"]
)
# Reflect on the web research
builder.add_edge("web_research", "reflection")
# Evaluate the research
builder.add_conditional_edges(
    "reflection", evaluate_research, ["web_research", "finalize_answer"]
)
# Finalize the answer
builder.add_edge("finalize_answer", END)

graph = builder.compile(name="pro-search-agent")
