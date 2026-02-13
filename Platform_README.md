# LangGraph 集成示例

当前示例演示了如何基于 LangGraph 构建复杂智能体工作流，并使用 AgentScope Runtime 运行。

## 准备工作

要运行此示例，您需要配置您的 DashScope API 密钥，请通过修改代码，或是通过配置`.env`的方式提供 DashScope API 密钥。

```python
os.environ["DASHSCOPE_API_KEY"] = "your-dashscope-api-key"
```

## 基于LangGraph构建的Agent服务

当前示例展示了如何基于 LangGraph 构建复杂智能体工作流，并使用 AgentScope Runtime 运行。

- 适配LangGraph的Agent构建方式

`AgentScopeRuntime` 适配了LangGraph，通过 `@query` 装饰LangGraph Agent执行函数可以通过AgentScopeRuntime 服务化运行，并且自动适配透出包括[A2A Protocol](https://a2a-protocol.org/), [AG-UI Protocol](https://docs.ag-ui.com/introduction), [Agent API Protocol](https://runtime.agentscope.io/en/protocol.html)等多种协议。

- 自定义服务Endpoint

通过`@endpoint`装饰器，可以自定义Agent服务透出的API，例如LangGraph Agent的状态，或是用户memory。

## API 端点

运行当前智能体示例时，以下 API 端点可用：

- `POST /process`

向智能体发送查询 （Agent API Protocol）

- `GET /short-term-memory/{session_id}`

获取指定会话的短期内存（对话历史）

- `GET /long-term-memory/{user_id}`

获取指定用户的长期内存

- `POST /ag-ui`

通过AG-UI Protocol向智能体发送查询

## 自定义

您可以通过以下方式自定义此示例：

1. **添加新工具**：在 `agent.py` 中使用 `@tool` 装饰器定义附加函数
2. **更改 LLM**：修改 `ChatOpenAI` 初始化以使用不同的模型或提供商
3. **扩展状态**：根据需要向 `CustomAgentState` 添加更多字段
4. **自定义内存**：将 `MemorySaver`/`InMemoryStore` 替换为持久化后端（如数据库存储）
5. **修改系统提示词**：更改 `prompt` 变量以自定义智能体行为

## 相关文档

- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [AgentScope Runtime 文档](https://runtime.agentscope.io/)
