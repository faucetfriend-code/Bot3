<objective>
Create a comprehensive checklist of all HTML elements in the trading bot interface to ensure they function properly. This checklist will serve as a reference for testing and validating that all interactive and display elements work correctly in the trading interface.
</objective>

<context>
This is for the trading bot project, specifically the main user interface file trading_bot_interface.html. The interface contains various elements for displaying market data, account information, trading controls, and real-time updates. The checklist will help identify which elements need testing and validation to ensure the interface is fully operational.
</context>

<requirements>
1. Read the trading_bot_interface.html file completely
2. Identify and list all HTML elements (tags) present in the file
3. For each element, note:
   - The tag name
   - Any relevant attributes (id, class, type, etc.)
   - Brief description of its purpose/functionality
   - Whether it's interactive (buttons, inputs, etc.) or display-only
4. Organize the list in a logical structure (group by sections if applicable)
5. Format as a checklist with checkboxes for each element
</requirements>

<output_format>
Create a markdown file with:
- Header showing total count of elements
- Sectioned checklist (group elements by functionality)
- Each item as: - [ ] Element: `<tag>` - Description - Attributes: [list]

Save the checklist to: ./checklists/html-elements-checklist.md
</output_format>

<verification>
Before completing, verify:
- All HTML tags from the file are included
- No duplicates in the list
- Each element has a clear description
- The file can be used as a practical checklist for testing
</verification>

<success_criteria>
- Comprehensive list covering all HTML elements in trading_bot_interface.html
- Clear, actionable checklist format
- File saved to the specified location
- Ready for use in testing and validation workflows
</success_criteria>