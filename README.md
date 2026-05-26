# Edu-Helper

SuperMew 是一个面向教师场景的多 Agent 教育 RAG 知识库问答与教学辅助系统。系统围绕教材资料入库、知识检索、教学内容生成和批改参考展开，结合 LangChain Agents、Milvus、Neo4j、PostgreSQL、Redis 与 OCR/视觉识别能力，提供教材问答、智能出题、教案生成、知识路径推理、主观题批改参考和客观题规则判分等功能。

## 核心能力

- **多 Agent 教学任务编排**：系统包含通用问答、智能出题、教案生成、批改参考和结果校验等专长 Agent，通过意图路由将教师请求分发到对应能力模块；知识图谱辅助能力由通用 Agent 按需调用图谱工具完成。
- **教育 RAG 检索增强**：自定义编排初始检索、相关性评估、查询重写和扩展检索流程，支持 Step-back Prompting、HyDE、复杂问题扩展和 RAG trace 追踪。
- **教材混合检索**：支持 PDF、Word、Excel 教材资料解析，采用 L1/L2/L3 三级滑动窗口分块，叶子分块写入 Milvus，父级分块写入 PostgreSQL，用于 Auto-merging 上下文合并。
- **Dense + Sparse Hybrid Search**：使用本地 BGE-M3 稠密向量和 BM25 稀疏向量，结合 Milvus Hybrid Search、RRF 融合召回、rerank 精排和 dense fallback 降级检索。
- **知识图谱增强推理**：使用 Neo4j 存储教材资源、chunk、知识点、概念、公式、方法及前置/相关/易混淆关系，支持知识点搜索、前置知识查询和教学路径生成。
- **OCR/视觉识别批改**：支持通过视觉模型从学生答案图片和标准答案图片中抽取客观题答案规则，并结合规则引擎完成批量判分、得分统计和教师可读报告生成。
- **教师材料管理**：支持保存出题结果、教案、作业、批改参考和客观题判分报告，并维护教师偏好记忆，用于教学内容生成时参考。
- **流式交互与过程可视化**：聊天接口基于 SSE 流式输出，前端实时展示 RAG 检索、评分、重写、二次召回等步骤。

## 系统架构

```text
离线入库：
教材 PDF / Word / Excel
 -> 文档解析 / OCR 识别
 -> L1/L2/L3 三级分块
 -> dense embedding + sparse embedding
 -> L3 写入 Milvus
 -> L1/L2 与资源元数据写入 PostgreSQL
 -> 知识点与关系写入 Neo4j

在线问答：
教师请求
 -> 意图路由
 -> 专长 Agent
 -> 教材检索 / 知识图谱 / 生成工具 / 批改工具
 -> 流式生成回答
 -> 保存会话、RAG trace 与教师材料
```

## 技术栈

- **后端框架**：Python、FastAPI、Pydantic、Uvicorn
- **Agent 编排**：LangChain Agents、意图路由、自定义 RAG 流程编排
- **RAG 检索**：Milvus、Hybrid Search、HNSW、SPARSE_INVERTED_INDEX、RRF、Rerank、Auto-merging
- **Embedding**：BAAI/bge-m3、langchain_huggingface、BM25 sparse embedding
- **知识图谱**：Neo4j、Cypher
- **数据存储**：PostgreSQL、Redis
- **文档处理**：PyPDFLoader、Docx2txtLoader、UnstructuredExcelLoader、OCR/视觉模型识别
- **前端交互**：Vue 3 CDN、SSE、marked、highlight.js
- **部署依赖**：Docker Compose、Milvus Standalone、MinIO、etcd

## 数据存储

```text
PostgreSQL：用户、会话、消息、资源元数据、父级 chunk、教师材料、教师偏好记忆
Milvus：教材叶子 chunk 的 dense embedding 与 sparse embedding
Redis：会话缓存、父级 chunk 缓存、任务进度、临时状态
Neo4j：知识点、概念、公式、方法、教材 chunk 引用和知识关系
```

### PostgreSQL 主要数据

```text
users
chat_sessions
chat_messages
resources
parent_chunks
teacher_artifacts
teacher_memories
```

### Neo4j 图谱节点与关系

节点类型：

```text
Resource
Chunk
KnowledgePoint
Concept
Formula
Method
```

关系类型：

```text
CONTAINS
FROM_CHUNK
PREREQUISITE_OF
RELATED_TO
APPLIES_TO
CONFUSABLE_WITH
```

## 多 Agent 设计

```text
教师请求
 └─ route_teacher_agent
     ├─ general：教材问答与通用教学辅助
     ├─ question_generator：智能出题、练习题、作业题
     ├─ lesson_planner：教案、教学流程、板书、课堂练习
     ├─ grading_assistant：批改参考、评分参考、错因分析
     └─ verifier：生成结果的证据与安全校验
```

每个 Agent 维护独立的 system prompt 和工具清单，工具层负责提供教材检索、图谱查询、结构化生成、教师材料保存和教师偏好记忆读取。

## RAG 流程

```text
retrieve_initial
 -> grade_documents
 -> generate_answer
```

当初始检索相关性不足时：

```text
retrieve_initial
 -> grade_documents
 -> rewrite_question
 -> retrieve_expanded
 -> generate_answer
```

检索链路包含：

- L3 叶子分块召回
- Dense + Sparse Hybrid Search
- RRF 融合排序
- rerank 精排
- L3 -> L2 -> L1 Auto-merging
- Step-back / HyDE 查询扩展
- RAG trace 记录与前端可视化

## 文档入库流程

1. 教师或管理员上传 PDF、Word、Excel 教材资料。
2. 系统保存资源元数据，并启动后台处理任务。
3. 文档解析器提取文本内容，生成 L1/L2/L3 三级 chunk。
4. L1/L2 父级 chunk 写入 PostgreSQL。
5. L3 叶子 chunk 生成 dense embedding 与 BM25 sparse embedding，写入 Milvus。
6. 知识抽取器从教材 chunk 中抽取知识点、概念、公式、方法和关系，写入 Neo4j。
7. 前端通过任务进度接口展示上传、清理、解析、向量化、图谱同步等阶段状态。

## 教师任务

### 教材问答

教师基于已入库教材提问，系统结合 RAG 与知识图谱生成回答，并保留来源页码、chunk id、检索分数和 RAG trace。

### 智能出题

教师输入知识点、题型、难度、数量、学科和年级，系统检索教材依据和图谱上下文，生成题干、答案、解析、评分点、难度和来源 chunk。

### 教案生成

系统根据教学主题、课时、年级、学科和教材上下文生成教案，覆盖教学目标、重点难点、课堂导入、教学流程、例题设计、课堂练习、板书设计和课后作业。

### 批改参考

教师提供题目、学生答案、标准答案或 rubric，系统给出参考得分、正确点、缺失点、错因分析、订正方向和置信度。

### 客观题规则判分

系统支持文本客观题批量判分，也支持从学生答案图片和标准答案图片中抽取答案信息，再通过规则引擎完成选择题、填空题、判断题等客观题判分。

## API 速览

### 鉴权

```text
POST /auth/register
POST /auth/login
GET  /auth/me
```

### 聊天与会话

```text
POST   /chat
POST   /chat/stream
GET    /sessions
GET    /sessions/{session_id}
DELETE /sessions/{session_id}
```

### 教师任务

```text
POST /teacher/questions/generate
POST /teacher/lesson-plans/generate
POST /teacher/grading/generate
POST /teacher/objective-grading/generate
POST /teacher/objective-grading/images
```

### 教师材料

```text
GET    /teacher/artifacts
POST   /teacher/artifacts
PATCH  /teacher/artifacts/{artifact_id}
DELETE /teacher/artifacts/{artifact_id}
```

### 知识图谱

```text
GET /knowledge-graph/search
```

### 文档资料

```text
GET    /documents
POST   /documents/upload
POST   /documents/upload/async
GET    /documents/upload/jobs/{job_id}
DELETE /documents/{filename}
```

## 本地部署

### 1. 环境准备

- Python 3.12+
- uv 或 pip
- Docker / Docker Compose

### 2. 安装依赖

```bash
uv sync
```

也可以使用 pip：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

### 3. 配置环境变量

在项目根目录创建 `.env`。`.env` 包含密钥和本地配置，不提交到 Git。

```env
# Model
ARK_API_KEY=your_api_key
MODEL=your_model_name
BASE_URL=https://your-llm-endpoint/v1
VISION_MODEL=your_vision_model_name

# Embedding
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cpu
DENSE_EMBEDDING_DIM=1024

# Rerank
RERANK_MODEL=your_rerank_model
RERANK_BINDING_HOST=https://your-rerank-host
RERANK_API_KEY=your_rerank_api_key

# Milvus
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
MILVUS_COLLECTION=embeddings_collection

# Database / Cache
DATABASE_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/langchain_app
REDIS_URL=redis://127.0.0.1:6379/0

# Neo4j
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=supermew-neo4j
NEO4J_DATABASE=neo4j

# Auth
JWT_SECRET_KEY=replace-with-strong-random-secret
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440
PASSWORD_PBKDF2_ROUNDS=310000

# RAG
AUTO_MERGE_ENABLED=true
AUTO_MERGE_THRESHOLD=2
LEAF_RETRIEVE_LEVEL=3
```

### 4. 启动基础服务

```bash
docker compose up -d
```

服务端口：

```text
PostgreSQL：5432
Redis：6379
Neo4j Browser：7474
Neo4j Bolt：7687
Milvus：19530
MinIO API：9000
MinIO Console：9001
Attu：8080
```

### 5. 启动应用

```bash
uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

访问地址：

```text
前端页面：http://127.0.0.1:8000/
API 文档：http://127.0.0.1:8000/docs
```

## 目录结构

```text
backend/
  app.py                         FastAPI 入口
  api.py                         API 路由
  agent.py                       Agent 创建、路由调用、流式对话
  rag_pipeline.py                RAG 检索、评分、查询改写与扩展召回流程
  rag_utils.py                   检索、rerank、查询扩展、Auto-merging
  tools.py                       教材检索工具与 RAG 步骤事件
  embedding.py                   dense embedding 与 BM25 sparse embedding
  milvus_client.py               Milvus 集合、混合检索、向量查询
  milvus_writer.py               文档向量写入
  document_loader.py             文档解析与三级分块
  parent_chunk_store.py          父级 chunk 存储
  knowledge_graph_builder.py     Neo4j 图谱写入
  neo4j_client.py                Neo4j 连接与查询封装
  models.py                      SQLAlchemy ORM
  schemas.py                     Pydantic 模型
  auth.py                        注册、登录、JWT、权限控制
  cache.py                       Redis 缓存封装
  education/
    agents/                      教师端专长 Agent
    generation_tools.py          出题、备课、批改参考工具
    graph_tools.py               知识图谱查询工具
    artifact_tools.py            教师材料与偏好记忆工具
    objective_grader.py          客观题规则判分
    objective_answer_extractor.py 文本/图片答案抽取
    knowledge_extractor.py       教材知识点与关系抽取

frontend/
  index.html
  script.js
  style.css

docker-compose.yml
pyproject.toml
```

## 交互形态

前端是一个 Vue 3 单页应用，包含登录、会话列表、流式聊天、RAG 过程展示、文档上传、任务进度和资料管理。后端通过 `StreamingResponse` 输出 SSE 事件，前端用 `ReadableStream` 逐块解析并渲染回答内容。

SSE 事件类型：

```text
agent_route：当前命中的专长 Agent
rag_step：实时检索步骤
content：模型输出 token
trace：完整 RAG trace
error：错误信息
[DONE]：流结束
```

## 项目特点

- 多 Agent 与 RAG 工作流结合，覆盖教师备课、出题、答疑和批改场景。
- Milvus Hybrid Search 同时利用语义向量和 BM25 关键词信号。
- 三级分块与 Auto-merging 兼顾精准召回和上下文完整性。
- Neo4j 知识图谱为教学路径、前置知识和易混淆知识提供结构化支撑。
- OCR/视觉识别能力支持图片答案抽取和客观题批量判分。
- PostgreSQL + Redis 支撑会话、材料、记忆和任务状态的持久化与缓存。
