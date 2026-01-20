Create a new tool router session

Copy page

POST
https://backend.composio.dev/api/v3/tool_router/session
POST
/api/v3/tool_router/session

Python

import requests
url = "https://backend.composio.dev/api/v3/tool_router/session"
payload = {
    "user_id": "user_987654321",
    "toolkits": { "enable": ["gmail", "slack"] }
}
headers = {
    "x-api-key": "<apiKey>",
    "Content-Type": "application/json"
}
response = requests.post(url, json=payload, headers=headers)
print(response.json())
Try it
201
Session with only specific toolkits

{
  "session_id": "trs_987654321",
  "mcp": {
    "type": "http",
    "url": "https://app.composio.dev/tool_router/v3/trs_987654321/mcp"
  },
  "tool_router_tools": [
    "GMAIL_SEND_EMAIL",
    "SLACK_POST_MESSAGE",
    "GMAIL_FETCH_EMAILS"
  ],
  "config": {
    "user_id": "user_987654321",
    "toolkits": {
      "enabled": [
        "gmail",
        "slack"
      ]
    },
    "auth_configs": {},
    "connected_accounts": {},
    "manage_connections": {
      "enabled": true,
      "callback_url": "https://your-app.com/auth/callback",
      "enable_wait_for_connections": false
    },
    "tools": {},
    "tags": {
      "enabled": [
        "readOnlyHint"
      ],
      "disabled": [
        "destructiveHint"
      ]
    },
    "workbench": {
      "proxy_execution_enabled": true,
      "auto_offload_threshold": 20000
    }
  }
}
Creates a new session for the tool router feature. This endpoint initializes a new session with specified toolkits and their authentication configurations. The session provides an isolated environment for testing and managing tool routing logic with scoped MCP server access.
Authentication
x-api-key
string
API key authentication
Request
This endpoint expects an object.
user_id
string
Required
>=1 character
The identifier of the user who is initiating the session, ideally a unique identifier from your database like a user ID or email address
toolkits
object
Optional
Toolkit configuration - specify either enable toolkits (allowlist) or disable toolkits (denylist). Mutually exclusive.


Show 2 variants
auth_configs
map from strings to strings
Optional
The auth configs to use for the session. This will override the default behavior and use the given auth config when specific toolkits are being executed
connected_accounts
map from strings to strings
Optional
The connected accounts to use for the session. This will override the default behaviour and use the given connected account when specific toolkits are being executed
manage_connections
object
Optional
Configuration for connection management settings

Show 3 properties
tools
map from strings to objects
Optional
Tool-level configuration per toolkit - either specify enable tools (whitelist), disable tools (blacklist), or filter by MCP tags for each toolkit


Show 3 variants
tags
list of enums or object
Optional
Global MCP tool annotation hints for filtering. Array format is treated as enabled list. Object format supports both enabled (tool must have at least one) and disabled (tool must NOT have any) lists. Toolkit-level tags override this. Toolkit enabled/disabled lists take precedence over tag filtering.


Show 2 variants
workbench
object
Optional
Configuration for workbench behavior

Show 2 properties
Response
Session successfully created. Returns the session ID and MCP server URL for the created session.
session_id
string
format: "toolRouterSessionId"
The identifier of the session
mcp
object

Show 2 properties
tool_router_tools
list of strings
List of available tools in this session
config
object
The session configuration including user, toolkits, and overrides

Show 8 properties
Errors

400
Bad Request Error

401
Unauthorized Error

403
Forbidden Error

500
Internal Server Error


####


Execute a tool within a tool router session

Copy page

POST
https://backend.composio.dev/api/v3/tool_router/session/:session_id/execute
POST
/api/v3/tool_router/session/:session_id/execute

Python

import requests
url = "https://backend.composio.dev/api/v3/tool_router/session/session_id/execute"
payload = { "tool_slug": "GITHUB_CREATE_ISSUE" }
headers = {
    "x-api-key": "<apiKey>",
    "Content-Type": "application/json"
}
response = requests.post(url, json=payload, headers=headers)
print(response.json())
Try it
200
Successful

{
  "data": {
    "message": "Hello, World!",
    "status": "success"
  },
  "error": "string",
  "log_id": "log_abc123xyz"
}
Executes a specific tool within a tool router session. The toolkit is automatically inferred from the tool slug. The tool must belong to an allowed toolkit and must not be disabled in the session configuration. This endpoint validates permissions, resolves connected accounts, and executes the tool with the session context.
Authentication
x-api-key
string
API key authentication
Path parameters
session_id
string
Required
Request
This endpoint expects an object.
tool_slug
string
Required
>=1 character
The unique slug identifier of the tool to execute
arguments
map from strings to any
Optional
The arguments required by the tool
Response
Successfully executed the tool. Returns execution result, logs, and status.
data
map from strings to any
The data returned by the tool execution
error
string or null
Error message if the execution failed, null otherwise
log_id
string
Unique identifier for the execution log
Errors

400
Bad Request Error

401
Unauthorized Error

403
Forbidden Error

404
Not Found Error

500
Internal Server Error

####


Execute a meta tool within a tool router session

Copy page

POST
https://backend.composio.dev/api/v3/tool_router/session/:session_id/execute_meta
POST
/api/v3/tool_router/session/:session_id/execute_meta

Python

import requests
url = "https://backend.composio.dev/api/v3/tool_router/session/trs_LX9uJKBinWWr/execute_meta"
payload = { "slug": "COMPOSIO_MANAGE_CONNECTIONS" }
headers = {
    "x-api-key": "<apiKey>",
    "Content-Type": "application/json"
}
response = requests.post(url, json=payload, headers=headers)
print(response.json())
Try it
200
Successful

{
  "data": {
    "message": "Hello, World!",
    "status": "success"
  },
  "error": "string",
  "log_id": "log_abc123xyz"
}
Executes a Composio meta tool (COMPOSIO_*) within a tool router session.

Authentication
x-api-key
string
API key authentication
Path parameters
session_id
string
Optional
format: "toolRouterSessionId"
Tool router session ID (required for public API, optional for internal - injected by middleware)

Request
This endpoint expects an object.
slug
enum
Required
The unique slug identifier of the meta tool to execute

Show 9 enum values
arguments
map from strings to any
Optional
The arguments required by the meta tool
Response
Successfully executed the meta tool. Returns execution result, logs, and status.
data
map from strings to any
The data returned by the tool execution
error
string or null
Error message if the execution failed, null otherwise
log_id
string
Unique identifier for the execution log
Errors

400
Bad Request Error

401
Unauthorized Error

403
Forbidden Error

404
Not Found Error

500
Internal Server Error

####


Get a tool router session by ID

Copy page

GET
https://backend.composio.dev/api/v3/tool_router/session/:session_id
GET
/api/v3/tool_router/session/:session_id

Python

import requests
url = "https://backend.composio.dev/api/v3/tool_router/session/trs_123456789"
headers = {"x-api-key": "<apiKey>"}
response = requests.get(url, headers=headers)
print(response.json())
Try it
200
Retrieved

{
  "session_id": "trs_987654321",
  "mcp": {
    "type": "http",
    "url": "https://app.composio.dev/tool_router/v3/trs_987654321/mcp"
  },
  "tool_router_tools": [
    "text-generation",
    "image-generation",
    "code-execution"
  ],
  "config": {
    "user_id": "user_42",
    "toolkits": {
      "enabled": [
        "default",
        "advanced_nlp"
      ]
    },
    "auth_configs": {
      "default": "authcfg_001",
      "advanced_nlp": "authcfg_002"
    },
    "connected_accounts": {
      "default": "acct_123",
      "advanced_nlp": "acct_456"
    },
    "manage_connections": {
      "enabled": true,
      "callback_url": "https://app.composio.dev/callbacks/connection",
      "enable_wait_for_connections": false
    },
    "tools": {
      "default": {
        "enabled": [
          "text-generation",
          "summarization"
        ]
      },
      "advanced_nlp": {
        "disabled": [
          "image-generation"
        ]
      }
    },
    "tags": {
      "enabled": [
        "readOnlyHint",
        "idempotentHint"
      ],
      "disabled": [
        "destructiveHint"
      ]
    },
    "workbench": {
      "proxy_execution_enabled": true,
      "auto_offload_threshold": 20000
    }
  }
}
Retrieves an existing tool router session by its ID. Returns the session configuration, MCP server URL, and available tools.
Authentication
x-api-key
string
API key authentication
Path parameters
session_id
string
Required
format: "toolRouterSessionId"
The unique identifier of the tool router session
Response
Session successfully retrieved. Returns the session details including configuration.
session_id
string
format: "toolRouterSessionId"
The identifier of the session
mcp
object

Show 2 properties
tool_router_tools
list of strings
List of available tools in this session
config
object
The session configuration including user, toolkits, and overrides

Show 8 properties
Errors

400
Bad Request Error

401
Unauthorized Error

404
Not Found Error

500
Internal Server Error



#####


Create a link session for a toolkit in a tool router session

Copy page

POST
https://backend.composio.dev/api/v3/tool_router/session/:session_id/link
POST
/api/v3/tool_router/session/:session_id/link

Python

import requests
url = "https://backend.composio.dev/api/v3/tool_router/session/trs_LX9uJKBinWWr/link"
payload = { "toolkit": "github" }
headers = {
    "x-api-key": "<apiKey>",
    "Content-Type": "application/json"
}
response = requests.post(url, json=payload, headers=headers)
print(response.json())
Try it
201
Created

{
  "link_token": "lt_abc123xyz",
  "redirect_url": "https://app.composio.dev/link/lt_abc123xyz",
  "connected_account_id": "ca_abc123xyz"
}
Initiates an authentication link session for a specific toolkit within a tool router session. Returns a link token and redirect URL that users can use to complete the OAuth flow.
Authentication
x-api-key
string
API key authentication
Path parameters
session_id
string
Required
format: "toolRouterSessionId"
The unique identifier of the tool router session
Request
This endpoint expects an object.
toolkit
string
Required
>=1 character
The unique slug identifier of the toolkit to connect
callback_url
string
Optional
format: "uri"
URL where users will be redirected after completing auth
Response
Successfully created link session. Returns link token, redirect URL, and connected account ID.
link_token
string
Token used to complete the authentication flow
redirect_url
string
format: "uri"
The URL where users should be redirected to complete OAuth
connected_account_id
string
format: "connectedAccountId"
The unique identifier for the connected account
Errors

400
Bad Request Error

401
Unauthorized Error

403
Forbidden Error

404
Not Found Error

500
Internal Server Error


#####


Get system prompt for a tool router session

Copy page

GET
https://backend.composio.dev/api/v3/tool_router/session/:session_id/prompt
GET
/api/v3/tool_router/session/:session_id/prompt

Python

import requests
url = "https://backend.composio.dev/api/v3/tool_router/session/trs_123456789/prompt"
headers = {"x-api-key": "<apiKey>"}
response = requests.get(url, headers=headers)
print(response.json())
Try it
200
Retrieved

{
  "prompt": "You are an AI agent that completes user tasks by calling Composio ToolRouter meta-tools..."
}
Returns the system prompt for a ToolRouter session, which can be injected into an agent prompt to improve reliability.
Authentication
x-api-key
string
API key authentication
Path parameters
session_id
string
Required
format: "toolRouterSessionId"
The unique identifier of the tool router session
Query parameters
model
string
Optional
The LLM model name for prompt customization
user_timezone
string
Optional
User’s timezone for date/time formatting

Response
Successfully retrieved the session system prompt.
prompt
string
The complete system prompt to use for the tool router session
Errors

400
Bad Request Error

401
Unauthorized Error

404
Not Found Error

500
Internal Server Error


#####

Get toolkits for a tool router session

Copy page

GET
https://backend.composio.dev/api/v3/tool_router/session/:session_id/toolkits
GET
/api/v3/tool_router/session/:session_id/toolkits

Python

import requests
url = "https://backend.composio.dev/api/v3/tool_router/session/trs_123456789/toolkits"
headers = {"x-api-key": "<apiKey>"}
response = requests.get(url, headers=headers)
print(response.json())
Try it
200
Retrieved

{
  "items": [
    {
      "name": "GitHub",
      "slug": "github",
      "enabled": true,
      "is_no_auth": false,
      "composio_managed_auth_schemes": [
        "oauth2"
      ],
      "meta": {
        "logo": "https://assets.composio.dev/logos/github.png",
        "description": "Connect your GitHub account to manage repositories and issues."
      },
      "connected_account": {
        "id": "ca_987654321",
        "user_id": "user_12345",
        "status": "connected",
        "created_at": "2024-01-15T09:30:00Z",
        "auth_config": {
          "id": "authcfg_54321",
          "auth_scheme": "oauth2",
          "is_composio_managed": true
        }
      }
    }
  ],
  "total_pages": 3,
  "current_page": 1,
  "total_items": 25,
  "next_cursor": "eyJwYWdlIjoxLCJsaW1pdCI6MTAwfQ=="
}
Retrieves a cursor-paginated list of toolkits available in the tool router session. Includes toolkit metadata, composio-managed auth schemes, and connected accounts if available. Optionally filter by specific toolkit slugs.

Authentication
x-api-key
string
API key authentication
Path parameters
session_id
string
Required
format: "toolRouterSessionId"
The unique identifier of the tool router session
Query parameters
limit
double
Optional
Number of items per page, max allowed is 1000
cursor
string
Optional
Cursor for pagination. The cursor is a base64 encoded string of the page and limit. The page is the page number and the limit is the number of items per page. The cursor is used to paginate through the items. The cursor is not required for the first page.
toolkits
list of strings
Optional
Optional comma-separated list of toolkit slugs to filter by. If provided, only these toolkits will be returned, overriding the session configuration.

is_connected
boolean
Optional
Defaults to false
Whether to filter by connected toolkits. If provided, only connected toolkits will be returned.
search
string
Optional
Search query to filter toolkits by name, slug, or description
Response
Toolkits successfully retrieved. Returns a paginated list of toolkits with their metadata and connected accounts.
items
list of objects

Show 7 properties
total_pages
double
current_page
double
total_items
double
next_cursor
string or null
Errors

400
Bad Request Error

401
Unauthorized Error

403
Forbidden Error

404
Not Found Error

500
Internal Server Error

####

List meta tools with schemas for a tool router session

Copy page

GET
https://backend.composio.dev/api/v3/tool_router/session/:session_id/tools
GET
/api/v3/tool_router/session/:session_id/tools

Python

import requests
url = "https://backend.composio.dev/api/v3/tool_router/session/session_id/tools"
payload = {}
headers = {
    "x-api-key": "<apiKey>",
    "Content-Type": "application/json"
}
response = requests.get(url, json=payload, headers=headers)
print(response.json())
Try it
200
Retrieved

{
  "items": [
    {
      "slug": "github-actions",
      "name": "GitHub Actions",
      "description": "Automate GitHub workflows including CI/CD, issue management, and release processes",
      "toolkit": {
        "slug": "github",
        "name": "GitHub",
        "logo": "https://github.githubassets.com/assets/GitHub-Mark-ea2971cee799.png"
      },
      "input_parameters": {
        "repo_name": {
          "type": "string",
          "description": "GitHub repository name in owner/repo format",
          "required": true,
          "example": "octocat/Hello-World"
        },
        "workflow_id": {
          "type": "string",
          "description": "ID or filename of the workflow to trigger",
          "required": true,
          "example": "main.yml"
        }
      },
      "no_auth": false,
      "available_versions": [
        "20250905_00",
        "20250906_00"
      ],
      "version": "20250905_00",
      "output_parameters": {
        "run_id": {
          "type": "number",
          "description": "ID of the workflow run that was triggered",
          "example": 12345678
        },
        "status": {
          "type": "string",
          "description": "Status of the workflow run",
          "example": "completed",
          "enum": [
            "queued",
            "in_progress",
            "completed",
            "failed"
          ]
        }
      },
      "scopes": [
        "https://www.googleapis.com/auth/gmail.modify"
      ],
      "tags": [
        "ci-cd",
        "github",
        "automation",
        "devops"
      ],
      "status": "active",
      "is_deprecated": false,
      "deprecated": {
        "displayName": "GitHub Actions",
        "version": "20250905_00",
        "available_versions": [
          "20250905_00",
          "20250906_00"
        ],
        "is_deprecated": false,
        "toolkit": {
          "logo": "https://github.githubassets.com/assets/GitHub-Mark-ea2971cee799.png"
        }
      }
    }
  ]
}
Returns the meta tools available in a tool router session with their complete schemas. This includes request and response schemas specific to the session context.
Authentication
x-api-key
string
API key authentication
Path parameters
session_id
string
Optional
format: "toolRouterSessionId"
Tool router session ID
Request
This endpoint expects an object.
Response
Successfully retrieved meta tools with their complete schemas.
items
list of objects
List of tools with their complete schemas

Show 14 properties
Errors

400
Bad Request Error

401
Unauthorized Error

403
Forbidden Error

404
Not Found Error

500
Internal Server Error