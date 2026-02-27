<objective>
Investigate why the trading bot server UI has reverted to an older version and is no longer serving the updated trading_bot_interface.html file. This affects the user experience as they expect to see their recent UI improvements, but instead see stale content from when the Docker container was built 3 weeks ago.
</objective>

<context>
This is a FastAPI-based trading bot application running in a Docker container. The issue appears to be that the Docker container was built weeks ago with an old version of the HTML interface, and the updated local file isn't being served because it's not properly mounted or served by the API server.

Key files to examine:
- @api_server.py - How the HTML file is served
- @docker-compose.yml - Volume mounts and container configuration  
- @Dockerfile - How the container image was built
- @trading_bot_interface.html - The current local version vs what's being served

The application runs on port 8000 and serves a web interface for trading bot management.
</context>

<data_sources>
@api_server.py - Check how static files are served and if trading_bot_interface.html is properly handled
@docker-compose.yml - Examine volume mounts to see if HTML file is excluded
@Dockerfile - Review build process to understand what files are included in the image
@trading_bot_interface.html - Verify the current local version and its modification time

!docker ps - Check running containers and their status
!docker inspect [container_id] - Examine container configuration
!stat trading_bot_interface.html - Check file modification time
!git log --oneline -10 trading_bot_interface.html - See recent changes to the file
</data_sources>

<analysis_requirements>
Thoroughly analyze the Docker container setup and file serving mechanism:

1. Determine how the HTML file is currently being served by the API server
2. Identify why the container is serving an old version instead of the updated local file
3. Check if the HTML file is mounted as a volume in docker-compose.yml
4. Examine the container build process to see what version of the file was baked into the image
5. Verify the file timestamps and git history to confirm the local file is newer

Consider multiple potential causes:
- Volume mounting configuration issues
- Static file serving configuration in FastAPI
- Container rebuild requirements
- File path resolution problems
</analysis_requirements>

<output_format>
Provide a comprehensive analysis in the following structure:

## Problem Summary
[Clear description of what's happening and why]

## Root Cause Analysis  
[Step-by-step investigation findings]

## Technical Details
- Container build date and included files
- Volume mount configuration
- File serving mechanism in api_server.py
- File modification timestamps

## Recommended Solutions
[Specific, actionable fixes with implementation steps]

Save the complete analysis to: ./diagnoses/ui-reversion-analysis.md
</output_format>

<verification>
Before completing the analysis, verify:
- All relevant files have been examined
- Container configuration is fully understood
- File timestamps and git history confirm the issue
- Multiple potential solutions are considered
- Recommendations are specific and implementable
</verification>

<success_criteria>
- Root cause of UI reversion is clearly identified
- Technical explanation matches the observed behavior
- At least 2-3 specific solution options are provided
- Analysis includes verification steps for each solution
- Output is saved to the specified file location
</success_criteria>