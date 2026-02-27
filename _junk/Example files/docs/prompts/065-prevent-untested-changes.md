<objective>
Implement comprehensive safeguards and automated testing practices to prevent the cycle of "fix one thing, break another" by ensuring all code changes are thoroughly tested before deployment. This will establish a robust development workflow that catches regressions early and maintains server stability.

The goal is to create a development environment where untested changes cannot reach production, preventing the frustrating pattern of immediate server breakage after each fix.
</objective>

<context>
This is a critical infrastructure issue in the trading bot project. The current development workflow allows untested changes to break the server repeatedly, creating a cycle where each fix introduces new problems. The project has:

- FastAPI backend with WebSocket support
- React-style HTML interface with real-time data streaming
- Comprehensive pytest test suite (25+ test files)
- CI/CD pipeline with linting, testing, and security scans
- Multi-platform testing across Ubuntu/Windows/macOS

The issue occurs because developers can make changes without running tests, leading to immediate server crashes or functionality breaks. This wastes significant time and creates frustration.

@pyproject.toml - Shows pytest and testing dependencies
@tests/README.md - Current testing documentation
@.github/workflows/ci.yml - CI/CD pipeline with testing
@AGENTS.md - Build and testing commands
</context>

<requirements>
Implement a multi-layered approach to prevent untested changes:

1. **Pre-commit Hooks**: Automatic testing before any commit
2. **Development Server Guards**: Runtime checks that prevent unsafe operations
3. **Test-Driven Development Enforcement**: Require tests for new features
4. **Automated Regression Detection**: Catch breaking changes immediately
5. **Code Quality Gates**: Prevent commits with failing tests or linting
6. **Environment-Specific Safeguards**: Different protections for dev/staging/prod

The solution must be comprehensive but not burdensome - developers should be able to work efficiently while being protected from their own mistakes.
</requirements>

<implementation>
<testing_infrastructure>
Thoroughly analyze the current testing setup and enhance it:

1. **Critical Path Testing**: Identify the most important tests that must pass for server stability
2. **Fast Feedback Loop**: Create quick-running tests for immediate feedback during development
3. **Integration Test Expansion**: Add more end-to-end tests that catch real-world breakage
4. **Test Data Management**: Ensure test databases and fixtures are reliable and fast

Consider multiple testing strategies:
- Unit tests for individual functions
- Integration tests for API endpoints
- End-to-end tests for complete workflows
- Performance regression tests
- WebSocket connection tests
</testing_infrastructure>

<pre_commit_protection>
Implement git hooks that prevent problematic commits:

1. **Pre-commit Hook**: Run critical tests before allowing commits
2. **Pre-push Hook**: Run full test suite before pushing to remote
3. **Commit Message Validation**: Ensure commits reference issues or tests
4. **Branch Protection**: Prevent direct pushes to main branch

The hooks should be:
- Fast enough not to interrupt workflow
- Comprehensive enough to catch real issues
- Configurable for different development scenarios
</pre_commit_protection>

<development_server_safeguards>
Add runtime protections to the development server:

1. **Health Check Integration**: Server refuses to start if critical components fail
2. **Configuration Validation**: Validate all required settings on startup
3. **Dependency Verification**: Check that all required services are available
4. **Database Connection Tests**: Verify database is accessible and schema is correct
5. **WebSocket Readiness**: Ensure WebSocket endpoints are properly configured

These safeguards should:
- Provide clear error messages about what's wrong
- Suggest specific fixes for common issues
- Allow development to continue with reduced functionality when possible
</development_server_safeguards>

<automated_testing_pipeline>
Enhance the CI/CD pipeline with additional safeguards:

1. **Staged Testing**: Run different test suites at different stages
2. **Dependency Impact Analysis**: Test related components when one changes
3. **Performance Baselines**: Detect performance regressions
4. **Security Gate**: Prevent deployment of insecure code
5. **Rollback Automation**: Quick rollback capability for failed deployments

Consider implementing:
- Test parallelization for faster feedback
- Smart test selection based on changed files
- Integration with code coverage tools
- Automated issue creation for test failures
</automated_testing_pipeline>

<code_quality_enforcement>
Implement code quality standards that prevent common mistakes:

1. **Type Checking**: Enforce mypy for all Python code
2. **Linting Standards**: Strict flake8/black rules
3. **Import Sorting**: Automated import organization
4. **Security Scanning**: Automated vulnerability detection
5. **Documentation Requirements**: Require docstrings for new functions

These should be:
- Integrated into the development workflow
- Fast to run locally
- Enforced in CI/CD
- Configurable for different code areas
</code_quality_enforcement>
</implementation>

<output>
Create the following files and modifications:

1. **Pre-commit Setup**:
   - `./.pre-commit-config.yaml` - Pre-commit hook configuration
   - `./scripts/setup-dev-environment.sh` - Development environment setup script
   - `./scripts/run-critical-tests.sh` - Fast critical test runner

2. **Server Safeguards**:
   - Modify `api_server.py` to add startup health checks
   - Add `./api_server_safeguards.py` - Server startup validation module

3. **Testing Infrastructure**:
   - `./tests/test_server_safeguards.py` - Tests for server safeguards
   - `./tests/test_critical_path.py` - Critical functionality tests
   - `./scripts/test-parallel.sh` - Parallel test runner

4. **Development Tools**:
   - `./scripts/dev-server.sh` - Enhanced development server with safeguards
   - `./scripts/check-ready-for-commit.sh` - Pre-commit readiness checker
   - `./Makefile` - Enhanced build targets for testing

5. **Documentation**:
   - `./docs/DEVELOPMENT_WORKFLOW.md` - Updated development guidelines
   - `./docs/TESTING_STRATEGY.md` - Comprehensive testing documentation

All scripts should be cross-platform (Windows/macOS/Linux compatible).
</output>

<constraints>
<critical_requirements>
- **Zero Breaking Changes**: The solution must not break existing functionality
- **Performance Impact**: Safeguards should not significantly slow down development
- **Developer Experience**: Tools should help developers, not hinder them
- **Gradual Adoption**: Allow teams to adopt safeguards incrementally
- **Clear Feedback**: When something fails, provide specific, actionable error messages
</critical_requirements>

<technical_constraints>
- **Python Compatibility**: Must work with Python 3.9-3.12 as per CI matrix
- **Cross-Platform**: Windows, macOS, and Linux support required
- **CI/CD Integration**: Must work with existing GitHub Actions workflow
- **Dependency Management**: Use existing tools (pytest, mypy, flake8, black)
- **Security**: No exposure of sensitive information in logs or error messages
</technical_constraints>

<why_these_constraints_matter>
These constraints ensure the solution is practical and maintainable. Breaking changes would defeat the purpose of preventing breakage. Performance impacts would make developers circumvent the safeguards. Poor developer experience leads to ignored tools. Gradual adoption prevents overwhelming teams. Clear feedback enables quick fixes rather than frustration.
</why_these_constraints_matter>
</constraints>

<verification>
Before declaring complete, verify the implementation:

1. **Functionality Tests**:
   - Run `pytest tests/test_critical_path.py` - should pass in < 30 seconds
   - Start server with `./scripts/dev-server.sh` - should show health check results
   - Try to commit without running tests - should be blocked by pre-commit hook

2. **Integration Tests**:
   - Run full CI pipeline locally: `python tasks.py lint && pytest`
   - Test WebSocket functionality remains intact
   - Verify all existing API endpoints still work

3. **Performance Validation**:
   - Measure test execution time before and after changes
   - Ensure server startup time hasn't increased significantly
   - Verify development workflow isn't noticeably slower

4. **Cross-Platform Testing**:
   - Test on Windows (current environment)
   - Verify scripts work on Linux/macOS (if possible)
   - Check that all tools integrate properly

5. **Documentation Review**:
   - Verify all new scripts have proper documentation
   - Check that development workflow is clearly explained
   - Ensure troubleshooting guides are included

<success_criteria>
The implementation is successful when:

- ✅ **Zero Regressions**: All existing tests pass, server starts normally
- ✅ **Active Protection**: Attempting to commit untested code is blocked
- ✅ **Fast Feedback**: Critical tests run in < 30 seconds during development
- ✅ **Clear Guidance**: Error messages provide specific, actionable fixes
- ✅ **Workflow Integration**: Tools integrate seamlessly with existing development process
- ✅ **Documentation**: New workflow is clearly documented and easy to follow

The development team can now make changes with confidence that breaking changes will be caught immediately, preventing the cycle of "fix one thing, break another."
</success_criteria>
</verification>

<success_criteria>
The implementation is successful when developers can no longer accidentally break the server with untested changes, while maintaining an efficient and pleasant development experience. The solution should prevent the frustrating cycle described in the user's request by catching issues at the earliest possible stage.
</success_criteria></content>
<parameter name="filePath">prompts/065-prevent-untested-changes.md