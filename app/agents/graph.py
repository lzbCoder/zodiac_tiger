from typing import Literal
from langgraph.graph import StateGraph, END

from app.state.agent_state import AgentState
from app.agents.task_manager import task_manager_node
from app.agents.dispatcher import dispatcher_node
from app.agents.travel_subgraph import build_travel_subgraph
from app.agents.assistant_subgraph import build_assistant_agent
from app.agents.memory_recall import memory_recall_node
from app.agents.memory_extraction import memory_extraction_node
from app.agents.document_agent import document_agent_node
from app.agents.artifact_store import artifact_store_node
from app.agents.chat_agent import chat_agent_node
from app.agents.error_policy import (
    DEFAULT_RETRY, NO_RETRY, log_and_raise,
    DEFAULT_TIMEOUT, LONG_TIMEOUT, SUBGRAPH_TIMEOUT,
)


def route_by_intent(state: AgentState) -> Literal["chat_agent", "travel_agent", "assistant_agent"]:
    intent = state.get("intent", "chat")
    if intent == "travel":
        return "travel_agent"
    elif intent == "assistant":
        return "assistant_agent"
    return "chat_agent"


def route_by_format(state: AgentState) -> Literal["document_agent", "__end__"]:
    fmt = state.get("generate_format", "")
    return "document_agent" if fmt and fmt != "none" else END


def _build_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    # 全局默认：重试 3 次（默认 default_retry_on 区分可恢复/不可恢复）+ 60s 超时 + 错误入库并中止
    workflow.set_node_defaults(
        retry_policy=DEFAULT_RETRY, 
        error_handler=log_and_raise, 
        timeout=DEFAULT_TIMEOUT   
    )

    workflow.add_node("memory_recall", memory_recall_node)
    workflow.add_node("task_manager", task_manager_node)   # 意图识别前的任务管理
    workflow.add_node("dispatcher", dispatcher_node)
    # 子图 wrapper：整图不重试、给宽松超时（避免 60s 误杀整个子图、避免重跑整图）
    workflow.add_node("travel_agent", build_travel_subgraph(),
                      retry_policy=NO_RETRY, timeout=SUBGRAPH_TIMEOUT)
    workflow.add_node("assistant_agent", build_assistant_agent(),
                      retry_policy=NO_RETRY, timeout=SUBGRAPH_TIMEOUT)
    workflow.add_node("chat_agent", chat_agent_node, timeout=LONG_TIMEOUT)  # 闲聊对话
    workflow.add_node("document_agent", document_agent_node,
                      retry_policy=NO_RETRY, timeout=LONG_TIMEOUT)          # 生成文件，不重试
    workflow.add_node("artifact_store", artifact_store_node,
                      retry_policy=NO_RETRY)                                # 产物入库，不重试
    workflow.add_node("memory_extraction", memory_extraction_node,
                      retry_policy=NO_RETRY)                                # 写库，不重试

    workflow.set_entry_point("memory_recall")
    workflow.add_edge("memory_recall", "task_manager")
    workflow.add_edge("task_manager", "dispatcher")

    workflow.add_conditional_edges(
        "dispatcher",
        route_by_intent,
        {
            "chat_agent": "chat_agent",
            "travel_agent": "travel_agent",
            "assistant_agent": "assistant_agent",
        },
    )

    # 各 agent 出口先经 artifact_store（重要成果落库），再到 memory_extraction
    workflow.add_conditional_edges(
        "chat_agent", route_by_format,
        {"document_agent": "document_agent", END: "artifact_store"},
    )
    workflow.add_conditional_edges(
        "travel_agent", route_by_format,
        {"document_agent": "document_agent", END: "artifact_store"},
    )
    workflow.add_conditional_edges(
        "assistant_agent", route_by_format,
        {"document_agent": "document_agent", END: "artifact_store"},
    )

    workflow.add_edge("document_agent", "artifact_store")
    workflow.add_edge("artifact_store", "memory_extraction")
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
