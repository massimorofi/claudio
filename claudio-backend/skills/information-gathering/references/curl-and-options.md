# cURL usage, options & error handling



## Prerequisites

Get your API key from: https://browserbase.com/settings

```bash
export BROWSERBASE_API_KEY="your_api_key"
```

## Using with cURL

```bash
curl -X POST "https://api.browserbase.com/v1/search" \
  -H "Content-Type: application/json" \
  -H "X-BB-API-Key: $BROWSERBASE_API_KEY" \
  -d '{"query": "browserbase web automation"}'
```

### Request Options

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | *required* | The search query |
| `numResults` | integer (1-25) | `10` | Number of results to return |

### Response

Returns JSON with:

| Field | Type | Description |
|-------|------|-------------|
| `requestId` | string | Unique identifier for the search request |
| `query` | string | The search query that was executed |
| `results` | array | List of search result objects |

Each result object contains:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier for the result |
| `url` | string | URL of the result |
| `title` | string | Title of the result |
| `author` | string? | Author of the content (if available) |
| `publishedDate` | string? | Publication date (if available) |
| `image` | string? | Image URL (if available) |
| `favicon` | string? | Favicon URL (if available) |

> **Note:** The `@browserbasehq/sdk` does not have a search method yet. Use cURL or direct HTTP calls.

## Common Options

### Limit number of results

```bash
curl -X POST "https://api.browserbase.com/v1/search" \
  -H "Content-Type: application/json" \
  -H "X-BB-API-Key: $BROWSERBASE_API_KEY" \
  -d '{"query": "web scraping best practices", "numResults": 5}'
```

## Error Handling

| Status | Meaning |
|--------|---------|
| 400 | Invalid request body (check query and parameters) |
| 403 | Invalid or missing API key |
| 429 | Rate limit exceeded (retry later) |
| 500 | Internal server error (retry later) |
