<objective>
Create a comprehensive catalog of all external API integrations in the trading bot system, focusing exclusively on production endpoints. Document each API call with detailed analysis against REST/HTTP best practices and performance optimization criteria.
</objective>

<context>
This catalog is for auditing and optimizing external API usage in the trading bot system. The primary external service is Pacifica Exchange, with potential others. Focus on production endpoints only - exclude test/mock endpoints and internal API proxies.

The catalog will help identify optimization opportunities, security improvements, and performance bottlenecks in external API integrations.
</context>

<data_sources>
@trading_bot_v2/pacifica_client.py - Main Pacifica REST API integration
@trading_bot_v2/pacifica_ws_client.py - WebSocket client for Pacifica real-time data
@trading_bot_v2/trading_bot.py - How external APIs are called in bot logic
![grep -r "requests\." trading_bot_v2/ --include="*.py"] - Find all HTTP client usage
![grep -r "websockets\." trading_bot_v2/ --include="*.py"] - Find WebSocket connections
![grep -r "https?://" trading_bot_v2/ --include="*.py"] - Find API endpoint URLs
</data_sources>

<analysis_requirements>
Thoroughly analyze each external API call against these criteria:

**Basic Information:**
- API Service (Pacifica Exchange, etc.)
- Endpoint URL and HTTP method
- Purpose and usage context
- Authentication method used

**REST/HTTP Best Practices Assessment:**
- Proper HTTP method usage (GET/POST/PUT/DELETE)
- REST compliance and resource modeling
- Appropriate HTTP status codes
- Content-Type headers and request/response formats
- Idempotent operations where applicable
- Cacheable responses

**Performance Optimization Assessment:**
- Connection reuse and pooling
- Efficient serialization (JSON efficiency)
- Compression usage
- Timeout configurations
- Rate limiting compliance
- Data volume considerations

**Security & Reliability Assessment:**
- Secure authentication methods
- Input validation and sanitization
- HTTPS usage consistently
- Error message safety (no sensitive data leaks)
- Retry logic and circuit breaker patterns
- Failure impact analysis

Use color-coded assessment: ✅ Good, ⚠️ Needs Improvement, ❌ Poor
</analysis_requirements>

<catalog_structure>
Create a markdown table for each API service with columns:
- Endpoint
- Method
- Purpose
- REST Assessment
- Performance Assessment
- Security Assessment
- Notes

Include implementation code snippets showing current usage patterns.
</catalog_structure>

<output_format>
Save comprehensive catalog to: ./external-api-catalog.md

Structure with:
- Executive summary with total endpoints and assessment scores
- Detailed catalog grouped by API service
- Assessment summary with top optimization opportunities
- Security/reliability concerns identified
- Performance bottlenecks and recommendations

Use markdown tables and clear headings for readability.
</output_format>

<verification>
Before completing, verify:
- All external API calls in production code are cataloged
- Each endpoint has complete assessment against all criteria
- Code snippets accurately reflect current implementation
- Assessment scores are consistent and justified
- Summary statistics are accurate
</verification>

<success_criteria>
- Complete catalog of all production external API endpoints
- Each endpoint assessed against all evaluation criteria
- Clear identification of optimization opportunities
- Actionable recommendations for improvements
- Professional documentation suitable for developer review
</success_criteria>