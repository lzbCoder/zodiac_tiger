from typing import Literal
from langgraph.graph import StateGraph, END

from app.state.agent_state import AgentState
from app.agents.dispatcher import dispatcher_node
from app.agents.report_subgraph import build_report_subgraph
from app.agents.travel_subgraph import build_travel_subgraph
from app.agents.assistant_subgraph import build_assistant_agent
from app.agents.memory_recall import memory_recall_node
from app.agents.memory_extraction import memory_extraction_node
from app.agents.document_agent import document_agent_node
from app.agents.chat_agent import chat_agent_node


def route_by_intent(state: AgentState) -> Literal["chat_agent", "report_agent", "travel_agent", "assistant_agent"]:
    intent = state.get("intent", "chat")
    if intent == "report":
        return "report_agent"
    elif intent == "travel":
        return "travel_agent"
    elif intent == "assistant":
        return "assistant_agent"
    return "chat_agent"


def route_by_format(state: AgentState) -> Literal["document_agent", "__end__"]:
    fmt = state.get("generate_format", "")
    return "document_agent" if fmt and fmt != "none" else END


def _build_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("memory_recall", memory_recall_node)
    workflow.add_node("dispatcher", dispatcher_node)
    workflow.add_node("report_agent", build_report_subgraph())  # 数据分析子图：继承主图 checkpointer
    workflow.add_node("travel_agent", build_travel_subgraph())  # 旅游规划子图：继承主图 checkpointer
    workflow.add_node("assistant_agent", build_assistant_agent())  # 综合助手子图：继承主图 checkpointer
    workflow.add_node("chat_agent", chat_agent_node)
    workflow.add_node("document_agent", document_agent_node)
    workflow.add_node("memory_extraction", memory_extraction_node)

    workflow.set_entry_point("memory_recall")
    workflow.add_edge("memory_recall", "dispatcher")

    workflow.add_conditional_edges(
        "dispatcher",
        route_by_intent,
        {
            "chat_agent": "chat_agent",
            "report_agent": "report_agent",
            "travel_agent": "travel_agent",
            "assistant_agent": "assistant_agent",
        },
    )

    workflow.add_conditional_edges(
        "chat_agent", route_by_format,
        {"document_agent": "document_agent", END: "memory_extraction"},
    )
    workflow.add_conditional_edges(
        "report_agent", route_by_format,
        {"document_agent": "document_agent", END: "memory_extraction"},
    )
    workflow.add_conditional_edges(
        "travel_agent", route_by_format,
        {"document_agent": "document_agent", END: "memory_extraction"},
    )
    workflow.add_conditional_edges(
        "assistant_agent", route_by_format,
        {"document_agent": "document_agent", END: "memory_extraction"},
    )

    workflow.add_edge("document_agent", "memory_extraction")
    workflow.add_edge("memory_extraction", END)

    return workflow


def build_graph(**kwargs) -> StateGraph:
    """供 langgraph dev 调用。忽略所有注入的 kwargs，返回无 checkpointer 的 graph。"""
    return _build_workflow().compile()


def build_graph_with_checkpointer(checkpointer):
    """供 main.py 调用，传入 PostgreSQL checkpointer。"""
    return _build_workflow().compile(checkpointer=checkpointer)


_agent_graph = None


def set_agent_graph(graph):
    global _agent_graph
    _agent_graph = graph


def get_agent_graph():
    if _agent_graph is None:
        raise RuntimeError("Agent graph 尚未初始化")
    return _agent_graph
