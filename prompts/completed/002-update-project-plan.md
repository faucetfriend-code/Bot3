<objective>
Update the project-plan.md to include comprehensive details on all API calls used throughout the system. Every API endpoint, parameter, response format, and error handling must be fully specified. Ensure the plan is completely mapped out from top to bottom with no missing pieces - every step, every line of code, every feature must be explicitly defined so developers have zero ambiguity.
</objective>

<context>
This is for refining the trading bot rebuild plan. The current plan lacks specific API call details, which could lead to implementation inconsistencies. The updated plan will serve as a complete blueprint for developers, ensuring all Pacifica.fi API interactions are precisely defined. This prevents developers from making assumptions about API usage.
</context>

<requirements>
1. Read the current project-plan.md file completely
2. Identify all components that interact with APIs (Pacifica client, trading bot, API server)
3. For each API call, specify:
   - Exact endpoint URL
   - HTTP method (GET, POST, DELETE)
   - Request headers (including authentication)
   - Request body format and required fields
   - Response format and all possible fields
   - Error response formats
   - Rate limiting considerations
   - Authentication method details
4. Update the task details section to include specific API call implementations
5. Add a new section detailing all API interactions
6. Ensure every function that makes API calls has complete parameter and return specifications
7. Add error handling details for each API call
8. Include mock response examples for testing
</requirements>

<implementation>
Thoroughly analyze the system architecture to identify every point of API interaction. For each API call, provide complete specifications including:

- Authentication: HMAC signature generation with timestamp, private key, and message format
- Request formatting: JSON body structure, URL encoding, query parameters
- Response parsing: Expected JSON structure, field types, optional vs required fields
- Error handling: HTTP status codes, error message formats, retry logic
- Rate limiting: Request frequency limits, backoff strategies

Explain why complete API specification matters: it eliminates ambiguity in implementation, ensures consistent error handling, and prevents integration issues that could arise from undocumented API behaviors.

Use extended thinking to deeply consider all possible API scenarios and edge cases.
</implementation>

<output>
Update the existing ./project-plan.md file with:
- New "API Specifications" section before "Implementation Phases"
- Detailed API call specifications for each endpoint used
- Updated task details with specific API implementation steps
- Complete parameter and response documentation
- Error handling specifications

Do not create a new file - modify the existing project-plan.md in place.
</output>

<verification>
Before declaring complete, verify:
- Every API call in the system is documented with full specifications
- All request/response formats are defined
- Error handling is specified for each call
- Authentication methods are detailed
- No API interactions are left unspecified
- The plan can be followed without any developer interpretation
</verification>

<success_criteria>
- All API calls fully documented with endpoints, methods, headers, bodies, responses
- Every function that uses APIs has complete input/output specifications
- Error handling defined for all API scenarios
- No "TBD" or ambiguous sections remain
- Plan is executable without external API documentation
</success_criteria>