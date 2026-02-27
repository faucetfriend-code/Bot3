<objective>
Conduct a comprehensive analysis of the trading bot codebase to identify inefficiencies, impractical implementations, and unoptimized sections. This analysis will help prioritize optimization efforts and improve system performance, maintainability, and resource utilization.
</objective>

<context>
This is a complex trading bot system with multiple components including:
- Python backend (API server, trading logic, database operations)
- HTML/JavaScript frontend interface
- SQLite database with complex queries
- WebSocket connections for real-time data
- Authentication and encryption systems
- Agent-based AI assistance tools

The system handles high-frequency trading operations, real-time market data, and complex financial calculations. Performance, reliability, and efficiency are critical for trading success.

@api_server.py - Main API server with FastAPI
@database.py - Database operations and connection pooling
@execution.py - Trading execution logic
@balance_manager.py - Balance and position management
@strategy.py - Trading strategy implementations
@market_data_feed.py - Market data processing
@frontend files - HTML/JS interface performance
</context>

<requirements>
Perform a thorough, systematic analysis of the entire codebase to identify:

1. **Performance Inefficiencies**
   - Database queries that could be optimized
   - Memory-intensive operations
   - CPU-bound calculations that could be cached
   - Network requests that could be batched or cached
   - Synchronous operations that should be asynchronous

2. **Algorithmic Inefficiencies**
   - O(n²) or worse algorithms that could be O(n) or O(log n)
   - Repeated calculations that could be memoized
   - Inefficient data structures (wrong list/dict usage)
   - Unnecessary data transformations

3. **Architectural Issues**
   - Tight coupling between components
   - Circular dependencies
   - Improper separation of concerns
   - Over-engineered solutions for simple problems

4. **Resource Management Problems**
   - Connection leaks (database, network)
   - Improper cleanup of resources
   - Memory leaks in long-running processes
   - Inefficient file I/O operations

5. **Code Quality Issues Affecting Performance**
   - Deep nesting that could be flattened
   - Large functions that should be split
   - Repeated code that could be abstracted
   - Exception handling that impacts performance

6. **Scalability Concerns**
   - Operations that won't scale with increased load
   - Single-threaded bottlenecks
   - Database queries that will slow with data growth
   - Memory usage that grows unbounded

7. **Practicality Assessment**
   - Overly complex solutions for simple problems
   - Features that add little value but significant complexity
   - Maintenance burdens that outweigh benefits

For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially.
</requirements>

<analysis_approach>
Thoroughly analyze each component using appropriate tools and methodologies:

- **Code Analysis**: Examine algorithms, data structures, and patterns
- **Performance Profiling**: Identify computational bottlenecks
- **Database Analysis**: Review query patterns and indexing
- **Memory Analysis**: Check for leaks and inefficient usage
- **Concurrency Review**: Assess async/await usage and threading
- **Architecture Review**: Evaluate system design decisions

After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding.

Go beyond basic code review - perform deep analysis of trading-specific performance requirements including real-time data processing, order execution speed, risk calculations, and market data feeds.
</analysis_approach>

<optimization_criteria>
Evaluate each finding against these criteria:

- **Impact**: How much performance/resource improvement is possible?
- **Effort**: How difficult/complex is the fix?
- **Risk**: What are the risks of changing this code?
- **Priority**: Critical for trading performance vs nice-to-have
- **Trading Relevance**: Does this affect order execution, risk management, or data processing?

Prioritize findings that affect:
1. Order execution speed (< 100ms target)
2. Real-time data processing latency
3. Risk calculation performance
4. Database query response times
5. Memory usage under sustained load
</optimization_criteria>

<output_format>
Create a comprehensive efficiency analysis report saved to: `./docs/effectimpact.md`

Structure the report with:

- Executive Summary (top optimization opportunities by impact)
- Performance Bottlenecks (critical issues affecting trading)
- Code Quality Improvements (maintainability optimizations)
- Architectural Recommendations (system design improvements)
- Resource Optimization (memory, CPU, network improvements)
- Scalability Assessment (growth and load handling)
- Implementation Roadmap (prioritized action items)

For each issue identified, include:
- **Location**: File and line number
- **Problem**: What's inefficient/impractical
- **Impact**: Performance/resource cost
- **Solution**: Specific optimization approach
- **Effort**: Implementation complexity (Low/Medium/High)
- **Priority**: Critical/High/Medium/Low
- **Risk**: Change risk assessment

Include specific code examples and quantitative metrics where possible.
</output_format>

<verification>
Before declaring complete, verify your assessment by:
- Cross-referencing findings across components
- Validating that recommendations are practical and implementable
- Ensuring all major performance-critical paths have been analyzed
- Confirming that trading-specific requirements are properly addressed

The analysis should provide actionable insights that can immediately improve system performance and maintainability.
</verification>

<success_criteria>
- All major components analyzed for efficiency issues
- Specific, actionable optimization recommendations provided
- Performance impact quantified where possible
- Implementation effort and risk assessed for each recommendation
- Trading-critical performance requirements addressed
- Report provides clear prioritization for optimization efforts
</success_criteria>