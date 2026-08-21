# API Test Report — Real End-to-End Run

**How:** local server `uvicorn main:app` (http://127.0.0.1:8000) — the exact same requests Swagger UI (`/docs` → *Try it out*) sends.  
**Test user:** `demotest@sunny.dev`  
**Result:** 10/10 requests returned HTTP 200 with real data.

> **Bug found & fixed during this test run** — on the first attempt both `/chat/stream/v2`
> chats shared one journey, and the second (database) question was answered with the
> *previous* question's reply. Root cause: `merge_history()` in
> `services/memory_service.py` kept only the **longest** of the memory-layer histories
> and silently dropped the incoming request messages whenever a stored thread was
> longer, so the orchestrator never saw the latest question — and the wrong reply was
> then written to the exact prompt cache. Fix: incoming request messages are now
> always appended to the merged history. After the fix (and a server restart) both
> agentic runs below route and execute correctly.

| # | Endpoint | Method | Status | Name |
| --- | --- | --- | --- | --- |
| 1 | `/health` | GET | 200 | Health check |
| 2 | `/connectors` | GET | 200 | Connector registry |
| 3 | `/connectors/legal_dictionary` | GET | 200 | Legal dictionary search |
| 4 | `/auth/login` | POST | 200 | Login |
| 5 | `/auth/me` | GET | 200 | Current user profile |
| 6 | `/journeys` | POST | 200 | Create journey |
| 7 | `/lawyers` | GET | 200 | Lawyer directory |
| 8 | `/chat/stream/v2` | POST | 200 | Agentic chat (assistant + dictionary tool) |
| 9 | `/chat/stream/v2` | POST | 200 | Agentic text-to-SQL (db_chat) |
| 10 | `/journeys/61ae697a-2310-4ca0-b8a8-639eb44bb15b` | DELETE | 200 | Delete journey |

---

## 1. Health check

**Request**

```http
GET /health HTTP/1.1
Host: 127.0.0.1:8000
```

**Response — HTTP 200**

```json
{
  "ok": true,
  "provider": "groq",
  "model": "openai/gpt-oss-120b",
  "configured": true,
  "stack": {
    "langchain": true,
    "langgraph": true,
    "multi_agent": true,
    "streaming": "langgraph.stream + multi_agent",
    "query_analyser": true,
    "auth": "mongodb",
    "roles": [
      "guest",
      "user",
      "lawyer",
      "admin"
    ],
    "memory": [
      "in_memory",
      "short_term_redis",
      "user_thread_mongo",
      "long_term_mongodb"
    ],
    "prompt_cache": true,
    "rag": true,
    "uploads": [
      "pdf",
      "docx",
      "text",
      "image"
    ],
    "embed_model": "nomic-embed-text-v1.5",
    "file_store": "mongodb_gridfs",
    "max_upload_mb": 5,
    "websocket_lawyer_connect": true
  },
  "agents": [
    {
      "name": "orchestrator",
      "description": "Root agent — analyses intent and routes to specialist agents",
      "handles": [
        "*"
      ]
    },
    {
      "name": "assistant",
      "description": "General legal Q&A — handles questions, procedures, and legal info",
      "handles": [
        "question",
        "procedure",
        "other"
      ]
    },
    {
      "name": "researcher",
      "description": "Deep legal research — case law, statutes, document review, comparisons",
      "handles": [
        "review",
        "compare"
      ]
    },
    {
      "name": "draft",
      "description": "Legal document drafting — notices, agreements, letters, petitions",
      "handles": [
        "draft"
      ]
    },
    {
      "name": "document_creator",
      "description": "Structured document creation — RTI, complaints, agreements, notices",
      "handles": [
        "document"
      ]
    },
    {
      "name": "lawyer_finder",
      "description": "Find lawyers by domain/jurisdiction and enable real-time chat",
      "handles": [
        "find_lawyer"
      ]
    },
    {
      "name": "db_chat",
      "description": "Answers questions about the lawyer directory by generating SQL against Neon Postgres",
      "handles": [
        "db_query"
      ]
    },
    {
      "name": "email",
      "description": "Professional legal email composition — client emails, notices, follow-ups",
      "handles": [
        "email"
      ]
    }
  ],
  "connectors": [
    {
      "name": "indian_kanoon",
      "available": false,
      "reason": "Connector not yet configured"
    },
    {
      "name": "bare_acts",
      "available": false,
      "reason": "Connector not yet configured"
    },
    {
      "name": "court_api",
      "available": false,
      "reason": "Connector not yet configured"
    },
    {
      "name": "legal_templates",
      "available": true,
      "templates": 4
    },
    {
      "name": "legal_dictionary",
      "available": true,
      "terms": 10
    },
    {
      "name": "neon_postgres",
      "available": true,
      "tables": 2
    }
  ],
  "memory": {
    "in_memory": {
      "ok": true,
      "store": "process",
      "sessions": 2
    },
    "short_term": {
      "ok": true,
      "store": "redis",
      "host": "redis-11344.c275.us-east-1-4.ec2.cloud.redislabs.com",
      "error": ""
    },
    "long_term": {
      "ok": true,
      "store": "mongodb",
      "db": "legal_assist_inhouse",
      "collection": "long_term_memory",
      "error": ""
    },
    "prompt_cache": {
      "ok": true,
      "store": "redis+memory",
      "ttl_seconds": 21600
    },
    "qdrant": {
      "ok": true,
      "store": "qdrant",
      "host": "https://4ba981d4-9abc-48f0-b042-f3c7a25228ee.eu-west-2-0.aws.cloud.qdrant.io",
      "collection": "legal_assist_docs"
    }
  },
  "qdrant": {
    "ok": true,
    "store": "qdrant",
    "host": "https://4ba981d4-9abc-48f0-b042-f3c7a25228ee.eu-west-2-0.aws.cloud.qdrant.io",
    "collection": "legal_assist_docs",
    "points": 6,
    "embed_model": "nomic-embed-text-v1.5",
    "error": ""
  },
  "embeddings": {
    "ok": true,
    "model": "nomic-embed-text-v1.5",
    "fastembed_model": "nomic-ai/nomic-embed-text-v1.5",
    "dim": 768,
    "provider": "fastembed",
    "error": "Error code: 404 - {'error': {'message': 'The model `nomic-embed-text-v1.5` does not exist or you do not have access to it.', 'type': 'invalid_request_error', 'code': 'model_not_found'}}"
  },
  "files": {
    "ok": true,
    "store": "mongodb_gridfs",
    "db": "legal_assist_inhouse",
    "bucket": "files",
    "files": 1,
    "collections": [
      "files.files",
      "files.chunks"
    ]
  }
}
```

---

## 2. Connector registry

**Request**

```http
GET /connectors HTTP/1.1
Host: 127.0.0.1:8000
```

**Response — HTTP 200**

```json
{
  "connectors": [
    {
      "name": "indian_kanoon",
      "available": false,
      "reason": "Connector not yet configured"
    },
    {
      "name": "bare_acts",
      "available": false,
      "reason": "Connector not yet configured"
    },
    {
      "name": "court_api",
      "available": false,
      "reason": "Connector not yet configured"
    },
    {
      "name": "legal_templates",
      "available": true,
      "templates": 4
    },
    {
      "name": "legal_dictionary",
      "available": true,
      "terms": 10
    },
    {
      "name": "neon_postgres",
      "available": true,
      "tables": 2
    }
  ]
}
```

---

## 3. Legal dictionary search

**Request**

```http
GET /connectors/legal_dictionary?query=mens%20rea HTTP/1.1
Host: 127.0.0.1:8000
```

**Response — HTTP 200**

```json
{
  "status": {
    "name": "legal_dictionary",
    "available": true,
    "terms": 10
  },
  "search": {
    "connector": "legal_dictionary",
    "available": true,
    "query": "mens rea",
    "results": [
      {
        "term": "mens rea",
        "definition": "The intention or knowledge of wrongdoing (guilty mind)."
      }
    ],
    "total": 1
  }
}
```

---

## 4. Login

**Request**

```http
POST /auth/login HTTP/1.1
Host: 127.0.0.1:8000
```
```json
{
  "email": "demotest@sunny.dev",
  "password": "********"
}
```

**Response — HTTP 200**

```json
{
  "token": "********redacted********",
  "user": {
    "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
    "email": "demotest@sunny.dev",
    "name": "Demo Test",
    "role": "user",
    "created_at": "2026-08-19T21:13:03.655704+00:00",
    "updated_at": "2026-08-19T21:13:03.655737+00:00"
  },
  "journey": {
    "journey_id": "8521cbd1-a853-4621-b29c-4d705f2253ec",
    "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
    "title": "API Test Journey",
    "title_locked": false,
    "created_at": "2026-08-21T07:11:45.136826+00:00",
    "updated_at": "2026-08-21T07:12:05.029328+00:00",
    "message_count": 2
  },
  "journeys": [
    {
      "journey_id": "8521cbd1-a853-4621-b29c-4d705f2253ec",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:11:45.136826+00:00",
      "updated_at": "2026-08-21T07:12:05.029328+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7beb7680-4704-4f4d-908f-53665d75e942",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:09:25.234773+00:00",
      "updated_at": "2026-08-21T07:09:37.152348+00:00",
      "message_count": 2
    },
    {
      "journey_id": "477bdab1-bea9-4661-bdaa-897799746244",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:02:51.530258+00:00",
      "updated_at": "2026-08-21T07:03:08.580758+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e6f2774f-e318-44c3-8755-2f485e5a8c2d",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Lawyers Overview",
      "title_locked": false,
      "created_at": "2026-08-20T06:26:47.278829+00:00",
      "updated_at": "2026-08-20T06:27:11.902709+00:00",
      "message_count": 2
    },
    {
      "journey_id": "605d238e-2bc8-4741-880e-d506b303b0f5",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Bail Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T23:01:07.206619+00:00",
      "updated_at": "2026-08-19T23:01:16.272172+00:00",
      "message_count": 2
    },
    {
      "journey_id": "16dcbccd-3dc4-47ba-8758-9b2b3a54fdca",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Fee Inquiry",
      "title_locked": false,
      "created_at": "2026-08-19T23:00:14.665948+00:00",
      "updated_at": "2026-08-19T23:00:58.530199+00:00",
      "message_count": 2
    },
    {
      "journey_id": "0c6cb8ea-c8fb-4e88-82de-d791dfed476f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Bail Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T22:55:50.181607+00:00",
      "updated_at": "2026-08-19T22:57:06.440554+00:00",
      "message_count": 2
    },
    {
      "journey_id": "5c594042-c8b5-4d71-b3a1-943a1a70cc5a",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Count and Fees",
      "title_locked": false,
      "created_at": "2026-08-19T22:55:32.577182+00:00",
      "updated_at": "2026-08-19T22:55:39.048311+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7c7f06c0-7bd5-4ca2-8d96-fd2b37a32cf8",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Count and Fees",
      "title_locked": false,
      "created_at": "2026-08-19T22:49:25.539567+00:00",
      "updated_at": "2026-08-19T22:49:56.390905+00:00",
      "message_count": 2
    },
    {
      "journey_id": "111f1da3-f456-4d08-b88d-5b12c1d519a4",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Delhi Criminal Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T22:48:22.021361+00:00",
      "updated_at": "2026-08-19T22:49:15.455064+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7d404027-c67c-4cec-9678-58bbd8d25d99",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity Verification Guidance",
      "title_locked": false,
      "created_at": "2026-08-19T22:24:20.744247+00:00",
      "updated_at": "2026-08-19T22:25:05.618020+00:00",
      "message_count": 3
    },
    {
      "journey_id": "22fcc783-378f-4857-917a-9a37477dde04",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity Verification Guidance",
      "title_locked": false,
      "created_at": "2026-08-19T22:19:12.853287+00:00",
      "updated_at": "2026-08-19T22:21:41.882860+00:00",
      "message_count": 4
    },
    {
      "journey_id": "0fe2eec7-d696-4b75-b9a4-e9aeb8d5f11c",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Indian Rental Agreement Basics",
      "title_locked": false,
      "created_at": "2026-08-19T22:17:59.357107+00:00",
      "updated_at": "2026-08-19T22:18:21.468145+00:00",
      "message_count": 2
    },
    {
      "journey_id": "2a0bcd72-2acc-4042-b566-7b14e267fa1b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Protections Against Unfair Landlords",
      "title_locked": false,
      "created_at": "2026-08-19T22:04:44.277999+00:00",
      "updated_at": "2026-08-19T22:04:58.774881+00:00",
      "message_count": 2
    },
    {
      "journey_id": "df31b7df-8345-42aa-a674-12a186deabb4",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights in India",
      "title_locked": false,
      "created_at": "2026-08-19T22:03:57.993399+00:00",
      "updated_at": "2026-08-19T22:04:07.460148+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e81383e8-b3a0-4bde-bc0c-038f448331b1",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Protections Against Unfair Landlords",
      "title_locked": false,
      "created_at": "2026-08-19T22:02:59.917662+00:00",
      "updated_at": "2026-08-19T22:03:35.261486+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7de07c55-400b-4dbd-a507-57d19a1a5f15",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights in India",
      "title_locked": false,
      "created_at": "2026-08-19T22:01:58.004245+00:00",
      "updated_at": "2026-08-19T22:02:32.302412+00:00",
      "message_count": 2
    },
    {
      "journey_id": "f5071a39-b401-4c59-8624-3a13a46da5fa",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Before Signing Lease",
      "title_locked": false,
      "created_at": "2026-08-19T22:01:16.417413+00:00",
      "updated_at": "2026-08-19T22:01:47.790834+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7bd441d3-48de-4f38-be6c-065813a19876",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Advice on Identity Verification",
      "title_locked": false,
      "created_at": "2026-08-19T22:00:12.039578+00:00",
      "updated_at": "2026-08-19T22:00:39.402230+00:00",
      "message_count": 2
    },
    {
      "journey_id": "24f3f606-5a34-4d7d-a7d8-f6b8becaa869",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:59:32.738921+00:00",
      "updated_at": "2026-08-19T21:59:46.852391+00:00",
      "message_count": 2
    },
    {
      "journey_id": "fdda5671-80c5-4532-8d55-6609ee04f4df",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:57:35.768901+00:00",
      "updated_at": "2026-08-19T21:57:49.709491+00:00",
      "message_count": 2
    },
    {
      "journey_id": "94893ad7-d57d-467b-95b8-266dfaff5036",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:56:38.119008+00:00",
      "updated_at": "2026-08-19T21:56:46.905141+00:00",
      "message_count": 2
    },
    {
      "journey_id": "99cd46d1-bcad-4ae3-baad-bc97ff46472b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Every Renter Should Know",
      "title_locked": false,
      "created_at": "2026-08-19T21:55:46.017455+00:00",
      "updated_at": "2026-08-19T21:56:00.665857+00:00",
      "message_count": 2
    },
    {
      "journey_id": "5d370345-9511-4620-aaa7-8955cf490d1b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:50:58.939962+00:00",
      "updated_at": "2026-08-19T21:51:13.372070+00:00",
      "message_count": 2
    },
    {
      "journey_id": "3c0b40b3-7dd9-4e1f-b225-8a7d79549432",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:49:57.720224+00:00",
      "updated_at": "2026-08-19T21:50:35.406700+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e353f3b4-c426-43a1-bdfd-036a91174215",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Every Renter Should Know",
      "title_locked": false,
      "created_at": "2026-08-19T21:48:53.328009+00:00",
      "updated_at": "2026-08-19T21:49:36.088041+00:00",
      "message_count": 2
    },
    {
      "journey_id": "a68d89a3-0761-482c-93f2-f616b7b811ac",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Understanding Tenant Rights Overview",
      "title_locked": false,
      "created_at": "2026-08-19T21:48:10.161968+00:00",
      "updated_at": "2026-08-19T21:48:18.729148+00:00",
      "message_count": 2
    },
    {
      "journey_id": "f6338991-cef0-43b4-875d-2a10f9318f17",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Applying for Right to Information",
      "title_locked": false,
      "created_at": "2026-08-19T21:41:07.483615+00:00",
      "updated_at": "2026-08-19T21:41:42.283331+00:00",
      "message_count": 2
    },
    {
      "journey_id": "2ff36447-165d-4ad8-a190-6f7e57ea148a",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "RTI Application Submission Process",
      "title_locked": false,
      "created_at": "2026-08-19T21:39:59.959060+00:00",
      "updated_at": "2026-08-19T21:40:35.145534+00:00",
      "message_count": 2
    },
    {
      "journey_id": "3af7d150-8876-437e-9a66-f241bb242af1",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Filing RTI Application in India",
      "title_locked": false,
      "created_at": "2026-08-19T21:24:25.667375+00:00",
      "updated_at": "2026-08-19T21:24:39.809776+00:00",
      "message_count": 2
    },
    {
      "journey_id": "b3059b82-8f74-44ec-9554-c98eaca8d1bd",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Filing RTI Application in India",
      "title_locked": false,
      "created_at": "2026-08-19T21:23:24.740570+00:00",
      "updated_at": "2026-08-19T21:23:59.438752+00:00",
      "message_count": 2
    },
    {
      "journey_id": "cf332c27-c3c5-4651-8759-5f9540a472a2",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Steps to File RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:22:43.445286+00:00",
      "updated_at": "2026-08-19T21:22:49.936318+00:00",
      "message_count": 2
    },
    {
      "journey_id": "616679e7-3c3a-4d23-8be6-8a1b51b8a41f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "How to File an RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:17:59.570877+00:00",
      "updated_at": "2026-08-19T21:18:32.788653+00:00",
      "message_count": 2
    },
    {
      "journey_id": "45f55d27-0c9d-45b1-9ad5-2819e3dce34f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Steps to File RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:13:28.736334+00:00",
      "updated_at": "2026-08-19T21:14:27.236180+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e569b3c8-9139-4080-8d8a-a11c7b575241",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "New chat",
      "title_locked": false,
      "created_at": "2026-08-19T21:13:04.370520+00:00",
      "updated_at": "2026-08-19T21:13:04.370520+00:00",
      "message_count": 0
    }
  ]
}
```

---

## 5. Current user profile

**Request**

```http
GET /auth/me HTTP/1.1
Host: 127.0.0.1:8000
```

**Response — HTTP 200**

```json
{
  "user": {
    "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
    "email": "demotest@sunny.dev",
    "name": "Demo Test",
    "role": "user",
    "created_at": "2026-08-19T21:13:03.655704+00:00",
    "updated_at": "2026-08-19T21:13:03.655737+00:00"
  },
  "journeys": [
    {
      "journey_id": "8521cbd1-a853-4621-b29c-4d705f2253ec",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:11:45.136826+00:00",
      "updated_at": "2026-08-21T07:12:05.029328+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7beb7680-4704-4f4d-908f-53665d75e942",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:09:25.234773+00:00",
      "updated_at": "2026-08-21T07:09:37.152348+00:00",
      "message_count": 2
    },
    {
      "journey_id": "477bdab1-bea9-4661-bdaa-897799746244",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:02:51.530258+00:00",
      "updated_at": "2026-08-21T07:03:08.580758+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e6f2774f-e318-44c3-8755-2f485e5a8c2d",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Lawyers Overview",
      "title_locked": false,
      "created_at": "2026-08-20T06:26:47.278829+00:00",
      "updated_at": "2026-08-20T06:27:11.902709+00:00",
      "message_count": 2
    },
    {
      "journey_id": "605d238e-2bc8-4741-880e-d506b303b0f5",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Bail Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T23:01:07.206619+00:00",
      "updated_at": "2026-08-19T23:01:16.272172+00:00",
      "message_count": 2
    },
    {
      "journey_id": "16dcbccd-3dc4-47ba-8758-9b2b3a54fdca",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Fee Inquiry",
      "title_locked": false,
      "created_at": "2026-08-19T23:00:14.665948+00:00",
      "updated_at": "2026-08-19T23:00:58.530199+00:00",
      "message_count": 2
    },
    {
      "journey_id": "0c6cb8ea-c8fb-4e88-82de-d791dfed476f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Bail Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T22:55:50.181607+00:00",
      "updated_at": "2026-08-19T22:57:06.440554+00:00",
      "message_count": 2
    },
    {
      "journey_id": "5c594042-c8b5-4d71-b3a1-943a1a70cc5a",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Count and Fees",
      "title_locked": false,
      "created_at": "2026-08-19T22:55:32.577182+00:00",
      "updated_at": "2026-08-19T22:55:39.048311+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7c7f06c0-7bd5-4ca2-8d96-fd2b37a32cf8",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Count and Fees",
      "title_locked": false,
      "created_at": "2026-08-19T22:49:25.539567+00:00",
      "updated_at": "2026-08-19T22:49:56.390905+00:00",
      "message_count": 2
    },
    {
      "journey_id": "111f1da3-f456-4d08-b88d-5b12c1d519a4",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Delhi Criminal Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T22:48:22.021361+00:00",
      "updated_at": "2026-08-19T22:49:15.455064+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7d404027-c67c-4cec-9678-58bbd8d25d99",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity Verification Guidance",
      "title_locked": false,
      "created_at": "2026-08-19T22:24:20.744247+00:00",
      "updated_at": "2026-08-19T22:25:05.618020+00:00",
      "message_count": 3
    },
    {
      "journey_id": "22fcc783-378f-4857-917a-9a37477dde04",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity Verification Guidance",
      "title_locked": false,
      "created_at": "2026-08-19T22:19:12.853287+00:00",
      "updated_at": "2026-08-19T22:21:41.882860+00:00",
      "message_count": 4
    },
    {
      "journey_id": "0fe2eec7-d696-4b75-b9a4-e9aeb8d5f11c",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Indian Rental Agreement Basics",
      "title_locked": false,
      "created_at": "2026-08-19T22:17:59.357107+00:00",
      "updated_at": "2026-08-19T22:18:21.468145+00:00",
      "message_count": 2
    },
    {
      "journey_id": "2a0bcd72-2acc-4042-b566-7b14e267fa1b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Protections Against Unfair Landlords",
      "title_locked": false,
      "created_at": "2026-08-19T22:04:44.277999+00:00",
      "updated_at": "2026-08-19T22:04:58.774881+00:00",
      "message_count": 2
    },
    {
      "journey_id": "df31b7df-8345-42aa-a674-12a186deabb4",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights in India",
      "title_locked": false,
      "created_at": "2026-08-19T22:03:57.993399+00:00",
      "updated_at": "2026-08-19T22:04:07.460148+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e81383e8-b3a0-4bde-bc0c-038f448331b1",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Protections Against Unfair Landlords",
      "title_locked": false,
      "created_at": "2026-08-19T22:02:59.917662+00:00",
      "updated_at": "2026-08-19T22:03:35.261486+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7de07c55-400b-4dbd-a507-57d19a1a5f15",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights in India",
      "title_locked": false,
      "created_at": "2026-08-19T22:01:58.004245+00:00",
      "updated_at": "2026-08-19T22:02:32.302412+00:00",
      "message_count": 2
    },
    {
      "journey_id": "f5071a39-b401-4c59-8624-3a13a46da5fa",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Before Signing Lease",
      "title_locked": false,
      "created_at": "2026-08-19T22:01:16.417413+00:00",
      "updated_at": "2026-08-19T22:01:47.790834+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7bd441d3-48de-4f38-be6c-065813a19876",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Advice on Identity Verification",
      "title_locked": false,
      "created_at": "2026-08-19T22:00:12.039578+00:00",
      "updated_at": "2026-08-19T22:00:39.402230+00:00",
      "message_count": 2
    },
    {
      "journey_id": "24f3f606-5a34-4d7d-a7d8-f6b8becaa869",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:59:32.738921+00:00",
      "updated_at": "2026-08-19T21:59:46.852391+00:00",
      "message_count": 2
    },
    {
      "journey_id": "fdda5671-80c5-4532-8d55-6609ee04f4df",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:57:35.768901+00:00",
      "updated_at": "2026-08-19T21:57:49.709491+00:00",
      "message_count": 2
    },
    {
      "journey_id": "94893ad7-d57d-467b-95b8-266dfaff5036",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:56:38.119008+00:00",
      "updated_at": "2026-08-19T21:56:46.905141+00:00",
      "message_count": 2
    },
    {
      "journey_id": "99cd46d1-bcad-4ae3-baad-bc97ff46472b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Every Renter Should Know",
      "title_locked": false,
      "created_at": "2026-08-19T21:55:46.017455+00:00",
      "updated_at": "2026-08-19T21:56:00.665857+00:00",
      "message_count": 2
    },
    {
      "journey_id": "5d370345-9511-4620-aaa7-8955cf490d1b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:50:58.939962+00:00",
      "updated_at": "2026-08-19T21:51:13.372070+00:00",
      "message_count": 2
    },
    {
      "journey_id": "3c0b40b3-7dd9-4e1f-b225-8a7d79549432",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:49:57.720224+00:00",
      "updated_at": "2026-08-19T21:50:35.406700+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e353f3b4-c426-43a1-bdfd-036a91174215",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Every Renter Should Know",
      "title_locked": false,
      "created_at": "2026-08-19T21:48:53.328009+00:00",
      "updated_at": "2026-08-19T21:49:36.088041+00:00",
      "message_count": 2
    },
    {
      "journey_id": "a68d89a3-0761-482c-93f2-f616b7b811ac",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Understanding Tenant Rights Overview",
      "title_locked": false,
      "created_at": "2026-08-19T21:48:10.161968+00:00",
      "updated_at": "2026-08-19T21:48:18.729148+00:00",
      "message_count": 2
    },
    {
      "journey_id": "f6338991-cef0-43b4-875d-2a10f9318f17",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Applying for Right to Information",
      "title_locked": false,
      "created_at": "2026-08-19T21:41:07.483615+00:00",
      "updated_at": "2026-08-19T21:41:42.283331+00:00",
      "message_count": 2
    },
    {
      "journey_id": "2ff36447-165d-4ad8-a190-6f7e57ea148a",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "RTI Application Submission Process",
      "title_locked": false,
      "created_at": "2026-08-19T21:39:59.959060+00:00",
      "updated_at": "2026-08-19T21:40:35.145534+00:00",
      "message_count": 2
    },
    {
      "journey_id": "3af7d150-8876-437e-9a66-f241bb242af1",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Filing RTI Application in India",
      "title_locked": false,
      "created_at": "2026-08-19T21:24:25.667375+00:00",
      "updated_at": "2026-08-19T21:24:39.809776+00:00",
      "message_count": 2
    },
    {
      "journey_id": "b3059b82-8f74-44ec-9554-c98eaca8d1bd",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Filing RTI Application in India",
      "title_locked": false,
      "created_at": "2026-08-19T21:23:24.740570+00:00",
      "updated_at": "2026-08-19T21:23:59.438752+00:00",
      "message_count": 2
    },
    {
      "journey_id": "cf332c27-c3c5-4651-8759-5f9540a472a2",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Steps to File RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:22:43.445286+00:00",
      "updated_at": "2026-08-19T21:22:49.936318+00:00",
      "message_count": 2
    },
    {
      "journey_id": "616679e7-3c3a-4d23-8be6-8a1b51b8a41f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "How to File an RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:17:59.570877+00:00",
      "updated_at": "2026-08-19T21:18:32.788653+00:00",
      "message_count": 2
    },
    {
      "journey_id": "45f55d27-0c9d-45b1-9ad5-2819e3dce34f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Steps to File RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:13:28.736334+00:00",
      "updated_at": "2026-08-19T21:14:27.236180+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e569b3c8-9139-4080-8d8a-a11c7b575241",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "New chat",
      "title_locked": false,
      "created_at": "2026-08-19T21:13:04.370520+00:00",
      "updated_at": "2026-08-19T21:13:04.370520+00:00",
      "message_count": 0
    }
  ],
  "journey_count": 35
}
```

---

## 6. Create journey

**Request**

```http
POST /journeys HTTP/1.1
Host: 127.0.0.1:8000
```
```json
{
  "title": "API Test Journey"
}
```

**Response — HTTP 200**

```json
{
  "journey_id": "b054b6ae-d8af-4485-8710-8e46231fef21",
  "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
  "title": "API Test Journey",
  "title_locked": false,
  "created_at": "2026-08-21T07:13:53.629256+00:00",
  "updated_at": "2026-08-21T07:13:53.629256+00:00",
  "message_count": 0,
  "messages": []
}
```

---

## 7. Lawyer directory

**Request**

```http
GET /lawyers HTTP/1.1
Host: 127.0.0.1:8000
```

**Response — HTTP 200**

```json
{
  "count": 14,
  "first_3": [
    {
      "id": 5,
      "name": "Adv. Vikram Singh",
      "specialisation": "Corporate Law",
      "city": "Delhi",
      "state": "Delhi",
      "experience_years": 21,
      "bar_council_id": "D/7890/2004",
      "fees_per_hearing": 12000.0,
      "rating": 4.9,
      "reviews_count": 88,
      "available_for_chat": true,
      "languages": "Hindi, English",
      "profile": "Corporate and commercial litigation partner-level counsel. Handles NCLT matters, shareholder disputes, contract enforcement, and arbitration for startups and SMEs."
    },
    {
      "id": 11,
      "name": "Adv. Suresh Patel",
      "specialisation": "Property Law",
      "city": "Ahmedabad",
      "state": "Gujarat",
      "experience_years": 24,
      "bar_council_id": "G/2468/2001",
      "fees_per_hearing": 8000.0,
      "rating": 4.8,
      "reviews_count": 71,
      "available_for_chat": false,
      "languages": "Gujarati, Hindi, English",
      "profile": "Veteran property lawyer — agricultural land conversion, society disputes, and builder-buyer litigation in Gujarat. Former government pleader."
    },
    {
      "id": 2,
      "name": "Adv. Priya Sharma",
      "specialisation": "Family Law",
      "city": "Mumbai",
      "state": "Maharashtra",
      "experience_years": 11,
      "bar_council_id": "M/5678/2015",
      "fees_per_hearing": 5000.0,
      "rating": 4.8,
      "reviews_count": 47,
      "available_for_chat": true,
      "languages": "Hindi, English, Marathi",
      "profile": "Family law specialist handling divorce, child custody, and maintenance matters in Mumbai family courts. Mediation-first approach; settles most matters without trial."
    }
  ]
}
```

---

## 8. Agentic chat (assistant + dictionary tool)

**Request**

```http
POST /chat/stream/v2 HTTP/1.1
Host: 127.0.0.1:8000
```
```json
{
  "messages": [
    {
      "role": "user",
      "content": "Explain 'caveat petition' briefly."
    }
  ],
  "journey_id": "b054b6ae-d8af-4485-8710-8e46231fef21"
}
```

**Response — HTTP 200**

*SSE stream — 31 events (thinking×6, flow×15, memory×1, cache×1, retrieval×1, agent_route×1, analysis×1, token×1, cache_write×1, memory_write×1, followups×1, done×1), completed in 20.6s.*

**Pipeline trace** (from the final `flow` event):

| Step | Status | Detail |
| --- | --- | --- |
| memory | done | 7 memory layer(s) · 8 fact(s) loaded |
| cache_exact | miss | No cached answer for this prompt |
| cache_semantic | skip | Semantic cache disabled (SEMANTIC_CACHE_ENABLED=false) |
| rag | miss | No relevant documents found |
| orchestrator | done | intent=question · domain=civil · complexity=simple → routed to assistant |
| assistant | done | Reply generated · 1573 characters · tools: define_legal_term |
| title | done | “Understanding Caveat Petition Basics” |
| followups | done | 3 question(s) generated from query + reply |
| cache_write | done | exact: Wrote Redis prompt cache, TTL 21600s · semantic: Semantic cache disabled |
| memory_write | done | 6 memory store(s) updated |

**Orchestrator analysis** (`agent_route` event):

```json
{
  "intent": "question",
  "domain": "civil",
  "complexity": "simple",
  "jurisdiction": "unspecified",
  "on_topic": true,
  "summary": "User wants a brief explanation of a caveat petition.",
  "refined_query": "Provide a brief explanation of what a caveat petition is.",
  "route_to": "assistant"
}
```

**Memory layers** (`memory` event, compact):

| Layer | Store | Status | Detail |
| --- | --- | --- | --- |
| In-memory | process RAM | miss | No process cache for this journey |
| Short-term | Redis | miss | No Redis session window |
| User thread | MongoDB journeys | miss | 0 message(s) in journey b054b6ae |
| Long-term | MongoDB | hit | 8 fact(s) from legal_assist_inhouse.long_term_memory |
| Semantic | qdrant | hit | 5 semantic fact(s) via vector similarity |
| Episodic | MongoDB | hit | 3 episode(s) recalled |
| Procedural | mongodb | hit | Preferences: format, jurisdiction |

**Final assistant reply** (1573 chars streamed via `token` events):

```
Hi Rahul,

Here’s a quick, plain‑language rundown of a **caveat petition** (as used in Indian law):

- **What it is:** A formal request filed with a court asking the court to **give you a warning (caveat)** before it proceeds with any order or decree that could affect your rights or interests.  
- **When it’s used:**  
  - When you anticipate that a suit, probate, or any other proceeding might be filed against you (e.g., a property dispute, succession case, or execution of a decree).  
  - To protect yourself from being taken by surprise by a judgment that could harm you.  
- **How it works:**  
  1. You file the caveat petition in the appropriate court, stating the matter you wish to be warned about.  
  2. The court records the caveat and **issues a notice to any other party** who later files a petition on the same subject.  
  3. The court **does not decide the case** at this stage; it merely ensures you are given an opportunity to appear and defend your interest before any order is passed.  
- **Validity:** A caveat remains effective for **six months** from the date of filing, unless you apply for an extension.  
- **Effect of non‑compliance:** If a court proceeds without giving you the required notice, the order may be **subject to challenge** for procedural irregularity.  

**Note:** This explanation is for general informational purposes and does not constitute formal legal advice. If you are considering filing a caveat petition or need help responding to one, it’s best to consult a qualified lawyer who can assess the specifics of your case.
```

**Stream close events:**

```json
{
  "followups": {
    "type": "followups",
    "questions": [
      "What are the procedural steps to file a caveat petition?",
      "How long does a caveat remain valid and can it be extended?",
      "Can a caveat be challenged or set aside by the opposite party?"
    ]
  },
  "done": {
    "type": "done",
    "model": "openai/gpt-oss-120b",
    "agent": "assistant"
  }
}
```

---

## 9. Agentic text-to-SQL (db_chat)

**Request**

```http
POST /chat/stream/v2 HTTP/1.1
Host: 127.0.0.1:8000
```
```json
{
  "messages": [
    {
      "role": "user",
      "content": "Show the 2 most experienced lawyers in Chennai"
    }
  ],
  "journey_id": "61ae697a-2310-4ca0-b8a8-639eb44bb15b"
}
```

**Response — HTTP 200**

*SSE stream — 32 events (thinking×6, flow×15, memory×1, cache×1, retrieval×1, agent_route×1, analysis×1, sql×1, token×1, cache_write×1, memory_write×1, followups×1, done×1), completed in 25.1s.*

**Pipeline trace** (from the final `flow` event):

| Step | Status | Detail |
| --- | --- | --- |
| memory | done | 7 memory layer(s) · 8 fact(s) loaded |
| cache_exact | miss | No cached answer for this prompt |
| cache_semantic | skip | Semantic cache disabled (SEMANTIC_CACHE_ENABLED=false) |
| rag | miss | No relevant documents found |
| orchestrator | done | intent=db_query · domain=general · complexity=simple → routed to db_chat |
| db_chat | done | SQL executed · 1 row(s) fetched: SELECT id, name, specialisation, city, state, experience_years, fees_per_hearing, rating, reviews_count, languages, prof |
| title | done | “Chennai Top Experienced Lawyers | Name | Specialisation | Experience (years) | F” |
| followups | done | 3 question(s) generated from query + reply |
| cache_write | done | exact: Wrote Redis prompt cache, TTL 21600s · semantic: Semantic cache disabled |
| memory_write | done | 6 memory store(s) updated |

**Orchestrator analysis** (`agent_route` event):

```json
{
  "intent": "db_query",
  "domain": "general",
  "complexity": "simple",
  "jurisdiction": "unspecified",
  "on_topic": true,
  "summary": "User wants the two most experienced lawyers in Chennai",
  "refined_query": "Show the two most experienced lawyers located in Chennai",
  "route_to": "db_chat"
}
```

**Executed SQL** (`sql` event — actual query run on Neon Postgres):

```json
[
  {
    "type": "sql",
    "sql": "SELECT id, name, specialisation, city, state, experience_years, fees_per_hearing, rating, reviews_count, languages, profile FROM lawyers WHERE city ILIKE 'Chennai' ORDER BY experience_years DESC, rating DESC LIMIT 2",
    "row_count": 1,
    "columns": [
      "id",
      "name",
      "specialisation",
      "city",
      "state",
      "experience_years",
      "fees_per_hearing",
      "rating",
      "reviews_count",
      "languages",
      "profile"
    ],
    "tables": [
      "lawyer_reviews",
      "lawyers"
    ]
  }
]
```

**Memory layers** (`memory` event, compact):

| Layer | Store | Status | Detail |
| --- | --- | --- | --- |
| In-memory | process RAM | miss | No process cache for this journey |
| Short-term | Redis | miss | No Redis session window |
| User thread | MongoDB journeys | miss | 0 message(s) in journey 61ae697a |
| Long-term | MongoDB | hit | 8 fact(s) from legal_assist_inhouse.long_term_memory |
| Semantic | qdrant | hit | 5 semantic fact(s) via vector similarity |
| Episodic | MongoDB | hit | 5 episode(s) recalled |
| Procedural | mongodb | hit | Preferences: format, jurisdiction |

**Final assistant reply** (611 chars streamed via `token` events):

```
**Here are the most experienced lawyer(s) we have for Chennai:**

| Name | Specialisation | Experience (years) | Fees per hearing | Rating | Reviews | Languages | Profile |
|------|----------------|--------------------|------------------|--------|---------|-----------|---------|
| Adv. Ananya Iyer | Employment Law | 8 | ₹4,000.00 | 4.40 ★ | 25 | Tamil, English | Advises employees and employers on wrongful termination, POSH compliance, gratuity and PF disputes. Appears before Chennai labour courts. |

*Only one lawyer matches the criteria in our current directory.*  

*Data is sample/demo directory data.*
```

**Stream close events:**

```json
{
  "followups": {
    "type": "followups",
    "questions": [
      "Can you show me other lawyers in Chennai?",
      "What is the fee structure for Adv. Ananya Iyer?",
      "Can I book an appointment with her?"
    ]
  },
  "done": {
    "type": "done",
    "model": "openai/gpt-oss-120b",
    "agent": "db_chat"
  }
}
```

---

## 10. Delete journey

**Request**

```http
DELETE /journeys/61ae697a-2310-4ca0-b8a8-639eb44bb15b HTTP/1.1
Host: 127.0.0.1:8000
```

**Response — HTTP 200**

```json
{
  "ok": true,
  "deleted": "61ae697a-2310-4ca0-b8a8-639eb44bb15b",
  "cleaned": {
    "docs": 0,
    "memory": {
      "in_memory": true,
      "short_term": true,
      "long_term": true
    }
  },
  "journey": {
    "journey_id": "b054b6ae-d8af-4485-8710-8e46231fef21",
    "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
    "title": "API Test Journey",
    "title_locked": false,
    "created_at": "2026-08-21T07:13:53.629256+00:00",
    "updated_at": "2026-08-21T07:14:15.405303+00:00",
    "message_count": 2
  },
  "journeys": [
    {
      "journey_id": "b054b6ae-d8af-4485-8710-8e46231fef21",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:13:53.629256+00:00",
      "updated_at": "2026-08-21T07:14:15.405303+00:00",
      "message_count": 2
    },
    {
      "journey_id": "8521cbd1-a853-4621-b29c-4d705f2253ec",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:11:45.136826+00:00",
      "updated_at": "2026-08-21T07:12:05.029328+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7beb7680-4704-4f4d-908f-53665d75e942",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:09:25.234773+00:00",
      "updated_at": "2026-08-21T07:09:37.152348+00:00",
      "message_count": 2
    },
    {
      "journey_id": "477bdab1-bea9-4661-bdaa-897799746244",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "API Test Journey",
      "title_locked": false,
      "created_at": "2026-08-21T07:02:51.530258+00:00",
      "updated_at": "2026-08-21T07:03:08.580758+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e6f2774f-e318-44c3-8755-2f485e5a8c2d",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Lawyers Overview",
      "title_locked": false,
      "created_at": "2026-08-20T06:26:47.278829+00:00",
      "updated_at": "2026-08-20T06:27:11.902709+00:00",
      "message_count": 2
    },
    {
      "journey_id": "605d238e-2bc8-4741-880e-d506b303b0f5",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Bail Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T23:01:07.206619+00:00",
      "updated_at": "2026-08-19T23:01:16.272172+00:00",
      "message_count": 2
    },
    {
      "journey_id": "16dcbccd-3dc4-47ba-8758-9b2b3a54fdca",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Fee Inquiry",
      "title_locked": false,
      "created_at": "2026-08-19T23:00:14.665948+00:00",
      "updated_at": "2026-08-19T23:00:58.530199+00:00",
      "message_count": 2
    },
    {
      "journey_id": "0c6cb8ea-c8fb-4e88-82de-d791dfed476f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Rated Bail Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T22:55:50.181607+00:00",
      "updated_at": "2026-08-19T22:57:06.440554+00:00",
      "message_count": 2
    },
    {
      "journey_id": "5c594042-c8b5-4d71-b3a1-943a1a70cc5a",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Count and Fees",
      "title_locked": false,
      "created_at": "2026-08-19T22:55:32.577182+00:00",
      "updated_at": "2026-08-19T22:55:39.048311+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7c7f06c0-7bd5-4ca2-8d96-fd2b37a32cf8",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Mumbai Lawyers Count and Fees",
      "title_locked": false,
      "created_at": "2026-08-19T22:49:25.539567+00:00",
      "updated_at": "2026-08-19T22:49:56.390905+00:00",
      "message_count": 2
    },
    {
      "journey_id": "111f1da3-f456-4d08-b88d-5b12c1d519a4",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Top Delhi Criminal Lawyers",
      "title_locked": false,
      "created_at": "2026-08-19T22:48:22.021361+00:00",
      "updated_at": "2026-08-19T22:49:15.455064+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7d404027-c67c-4cec-9678-58bbd8d25d99",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity Verification Guidance",
      "title_locked": false,
      "created_at": "2026-08-19T22:24:20.744247+00:00",
      "updated_at": "2026-08-19T22:25:05.618020+00:00",
      "message_count": 3
    },
    {
      "journey_id": "22fcc783-378f-4857-917a-9a37477dde04",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity Verification Guidance",
      "title_locked": false,
      "created_at": "2026-08-19T22:19:12.853287+00:00",
      "updated_at": "2026-08-19T22:21:41.882860+00:00",
      "message_count": 4
    },
    {
      "journey_id": "0fe2eec7-d696-4b75-b9a4-e9aeb8d5f11c",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Indian Rental Agreement Basics",
      "title_locked": false,
      "created_at": "2026-08-19T22:17:59.357107+00:00",
      "updated_at": "2026-08-19T22:18:21.468145+00:00",
      "message_count": 2
    },
    {
      "journey_id": "2a0bcd72-2acc-4042-b566-7b14e267fa1b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Protections Against Unfair Landlords",
      "title_locked": false,
      "created_at": "2026-08-19T22:04:44.277999+00:00",
      "updated_at": "2026-08-19T22:04:58.774881+00:00",
      "message_count": 2
    },
    {
      "journey_id": "df31b7df-8345-42aa-a674-12a186deabb4",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights in India",
      "title_locked": false,
      "created_at": "2026-08-19T22:03:57.993399+00:00",
      "updated_at": "2026-08-19T22:04:07.460148+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e81383e8-b3a0-4bde-bc0c-038f448331b1",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Protections Against Unfair Landlords",
      "title_locked": false,
      "created_at": "2026-08-19T22:02:59.917662+00:00",
      "updated_at": "2026-08-19T22:03:35.261486+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7de07c55-400b-4dbd-a507-57d19a1a5f15",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights in India",
      "title_locked": false,
      "created_at": "2026-08-19T22:01:58.004245+00:00",
      "updated_at": "2026-08-19T22:02:32.302412+00:00",
      "message_count": 2
    },
    {
      "journey_id": "f5071a39-b401-4c59-8624-3a13a46da5fa",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Before Signing Lease",
      "title_locked": false,
      "created_at": "2026-08-19T22:01:16.417413+00:00",
      "updated_at": "2026-08-19T22:01:47.790834+00:00",
      "message_count": 2
    },
    {
      "journey_id": "7bd441d3-48de-4f38-be6c-065813a19876",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Advice on Identity Verification",
      "title_locked": false,
      "created_at": "2026-08-19T22:00:12.039578+00:00",
      "updated_at": "2026-08-19T22:00:39.402230+00:00",
      "message_count": 2
    },
    {
      "journey_id": "24f3f606-5a34-4d7d-a7d8-f6b8becaa869",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:59:32.738921+00:00",
      "updated_at": "2026-08-19T21:59:46.852391+00:00",
      "message_count": 2
    },
    {
      "journey_id": "fdda5671-80c5-4532-8d55-6609ee04f4df",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:57:35.768901+00:00",
      "updated_at": "2026-08-19T21:57:49.709491+00:00",
      "message_count": 2
    },
    {
      "journey_id": "94893ad7-d57d-467b-95b8-266dfaff5036",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:56:38.119008+00:00",
      "updated_at": "2026-08-19T21:56:46.905141+00:00",
      "message_count": 2
    },
    {
      "journey_id": "99cd46d1-bcad-4ae3-baad-bc97ff46472b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Every Renter Should Know",
      "title_locked": false,
      "created_at": "2026-08-19T21:55:46.017455+00:00",
      "updated_at": "2026-08-19T21:56:00.665857+00:00",
      "message_count": 2
    },
    {
      "journey_id": "5d370345-9511-4620-aaa7-8955cf490d1b",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:50:58.939962+00:00",
      "updated_at": "2026-08-19T21:51:13.372070+00:00",
      "message_count": 2
    },
    {
      "journey_id": "3c0b40b3-7dd9-4e1f-b225-8a7d79549432",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Legal Identity and Rights",
      "title_locked": false,
      "created_at": "2026-08-19T21:49:57.720224+00:00",
      "updated_at": "2026-08-19T21:50:35.406700+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e353f3b4-c426-43a1-bdfd-036a91174215",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Tenant Rights Every Renter Should Know",
      "title_locked": false,
      "created_at": "2026-08-19T21:48:53.328009+00:00",
      "updated_at": "2026-08-19T21:49:36.088041+00:00",
      "message_count": 2
    },
    {
      "journey_id": "a68d89a3-0761-482c-93f2-f616b7b811ac",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Understanding Tenant Rights Overview",
      "title_locked": false,
      "created_at": "2026-08-19T21:48:10.161968+00:00",
      "updated_at": "2026-08-19T21:48:18.729148+00:00",
      "message_count": 2
    },
    {
      "journey_id": "f6338991-cef0-43b4-875d-2a10f9318f17",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Applying for Right to Information",
      "title_locked": false,
      "created_at": "2026-08-19T21:41:07.483615+00:00",
      "updated_at": "2026-08-19T21:41:42.283331+00:00",
      "message_count": 2
    },
    {
      "journey_id": "2ff36447-165d-4ad8-a190-6f7e57ea148a",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "RTI Application Submission Process",
      "title_locked": false,
      "created_at": "2026-08-19T21:39:59.959060+00:00",
      "updated_at": "2026-08-19T21:40:35.145534+00:00",
      "message_count": 2
    },
    {
      "journey_id": "3af7d150-8876-437e-9a66-f241bb242af1",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Filing RTI Application in India",
      "title_locked": false,
      "created_at": "2026-08-19T21:24:25.667375+00:00",
      "updated_at": "2026-08-19T21:24:39.809776+00:00",
      "message_count": 2
    },
    {
      "journey_id": "b3059b82-8f74-44ec-9554-c98eaca8d1bd",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Filing RTI Application in India",
      "title_locked": false,
      "created_at": "2026-08-19T21:23:24.740570+00:00",
      "updated_at": "2026-08-19T21:23:59.438752+00:00",
      "message_count": 2
    },
    {
      "journey_id": "cf332c27-c3c5-4651-8759-5f9540a472a2",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Steps to File RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:22:43.445286+00:00",
      "updated_at": "2026-08-19T21:22:49.936318+00:00",
      "message_count": 2
    },
    {
      "journey_id": "616679e7-3c3a-4d23-8be6-8a1b51b8a41f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "How to File an RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:17:59.570877+00:00",
      "updated_at": "2026-08-19T21:18:32.788653+00:00",
      "message_count": 2
    },
    {
      "journey_id": "45f55d27-0c9d-45b1-9ad5-2819e3dce34f",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "Steps to File RTI",
      "title_locked": false,
      "created_at": "2026-08-19T21:13:28.736334+00:00",
      "updated_at": "2026-08-19T21:14:27.236180+00:00",
      "message_count": 2
    },
    {
      "journey_id": "e569b3c8-9139-4080-8d8a-a11c7b575241",
      "user_id": "9b2312cb-5f78-4e83-b6c6-8f76c8dae4d2",
      "title": "New chat",
      "title_locked": false,
      "created_at": "2026-08-19T21:13:04.370520+00:00",
      "updated_at": "2026-08-19T21:13:04.370520+00:00",
      "message_count": 0
    }
  ]
}
```
