<objective>
Analyze the HTML elements checklist to determine which sections are complete (elements working properly) and which still need work. This analysis will help prioritize development efforts and identify which interface components require attention.
</objective>

<context>
This is for the trading bot project where we created a comprehensive checklist of all HTML elements in trading_bot_interface.html. The checklist serves as a reference for ensuring all interface elements function correctly. We need to assess the current state to understand what's working and what needs improvement.
</context>

<data_sources>
@./checklists/html-elements-checklist.md - The comprehensive HTML elements checklist
@trading_bot_interface.html - The main interface file to cross-reference functionality
</data_sources>

<analysis_requirements>
1. Read the complete HTML elements checklist
2. For each major section (Navigation, Authentication, Dashboard, etc.), assess:
   - Which elements are confirmed working
   - Which elements may need testing or fixes
   - Any missing functionality or broken features
3. Consider the interface's current operational status
4. Identify patterns in incomplete sections
5. Prioritize sections based on criticality to core functionality
</analysis_requirements>

<output_format>
Create a status report with:

## Executive Summary
- Total sections analyzed
- Completion percentages
- Critical issues identified

## Section-by-Section Analysis
For each major section:
- **Status**: Complete/Incomplete/Needs Testing
- **Working Elements**: List confirmed functional elements
- **Issues Found**: Elements needing work
- **Priority**: High/Medium/Low for fixing

## Recommendations
- Next steps for incomplete sections
- Testing priorities
- Development focus areas

Save the analysis to: ./analyses/checklist-completion-status.md
</output_format>

<verification>
Before completing, verify:
- All major sections from the checklist have been analyzed
- Status assessments are based on actual interface functionality
- Recommendations are actionable and prioritized
- The report provides clear guidance for next steps
</verification>

<success_criteria>
- Comprehensive analysis of all checklist sections
- Clear identification of complete vs incomplete elements
- Actionable recommendations for remaining work
- Report saved to the specified location and ready for development team use
</success_criteria>