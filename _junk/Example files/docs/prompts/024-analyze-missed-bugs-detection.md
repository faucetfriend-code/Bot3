<objective>
Analyze the comprehensive bugs report in missedbugs.md to understand why 35 additional critical bugs were missed in the original bugs.md, and develop improved bug detection strategies for future development to prevent similar oversights.
</objective>

<context>
This is for a trading bot project where an initial bugs.md cataloged 12 issues, but a deeper audit revealed 35 additional undocumented bugs including 9 critical and 9 high-priority issues. Understanding why these were missed and implementing better detection methods is crucial for improving code quality and preventing production failures.

@missedbugs.md - comprehensive audit results showing missed bugs
@bugs.md - original bugs catalog that missed many issues
</context>

<requirements>
1. Compare missedbugs.md findings with bugs.md to identify patterns of missed issues
2. Analyze root causes for why critical bugs were overlooked
3. Develop improved bug detection methodologies and tools
4. Create actionable recommendations for preventing similar oversights
5. Propose automated detection strategies for future development
</requirements>

<implementation>
Thoroughly analyze both bug reports to identify detection gaps. For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially.

Go beyond basic comparison - identify systemic issues in bug detection approach and propose comprehensive improvements.

After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding.
</implementation>

<output>
Create analysis report with relative path:
- ./diagnoses/bug-detection-analysis.md - comprehensive analysis of missed bugs and improved detection strategies
</output>

<verification>
Before declaring complete:
1. Verify all missed bug categories are analyzed
2. Ensure proposed detection methods are practical and actionable
3. Check that recommendations address root causes, not just symptoms
4. Validate that proposed tools and processes are feasible for the project
</verification>

<success_criteria>
- Root causes of missed bugs clearly identified
- Improved detection methodologies proposed
- Actionable recommendations for preventing future oversights
- Analysis covers all major bug categories from missedbugs.md
</success_criteria>