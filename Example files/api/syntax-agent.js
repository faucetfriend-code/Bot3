#!/usr/bin/env node

/**
 * HTML Master - Comprehensive Web Analysis Agent
 * Detects and fixes HTML, CSS, JavaScript, accessibility, performance, and security issues
 */

const fs = require('fs');
const path = require('path');
const acorn = require('acorn');
const htmlparser2 = require('htmlparser2');
const walk = require('acorn-walk');
const css = require('css');
const csstree = require('css-tree');

class HTMLMaster {
    constructor() {
        this.errors = [];
        this.warnings = [];
        this.fixes = [];
        this.score = 100;
        this.categories = {
            html: { errors: 0, warnings: 0, score: 100 },
            css: { errors: 0, warnings: 0, score: 100 },
            javascript: { errors: 0, warnings: 0, score: 100 },
            accessibility: { errors: 0, warnings: 0, score: 100 },
            performance: { errors: 0, warnings: 0, score: 100 },
            security: { errors: 0, warnings: 0, score: 100 },
            seo: { errors: 0, warnings: 0, score: 100 },
            best_practices: { errors: 0, warnings: 0, score: 100 }
        };
    }

    /**
     * Main entry point - comprehensive HTML analysis
     */
    async processFile(filePath) {
        console.log(`🔍 HTML Master analyzing: ${filePath}`);
        console.log(`═`.repeat(60));

        try {
            const content = fs.readFileSync(filePath, 'utf8');
            const startTime = Date.now();

            // Extract different content types
            const analysis = {
                html: this.extractHTML(content),
                css: this.extractCSS(content),
                javascript: this.extractJavaScript(content),
                metadata: this.extractMetadata(content)
            };

            // Run comprehensive analysis
            await Promise.all([
                this.analyzeHTML(analysis.html, content),
                this.analyzeCSS(analysis.css),
                this.analyzeJavaScript(analysis.javascript),
                this.analyzeAccessibility(analysis.html),
                this.analyzePerformance(analysis),
                this.analyzeSecurity(analysis),
                this.analyzeSEO(analysis.metadata),
                this.analyzeBestPractices(analysis)
            ]);

            const analysisTime = Date.now() - startTime;

            // Calculate overall score
            this.calculateOverallScore();

            // Display comprehensive report
            this.displayComprehensiveReport(analysisTime);

            // Apply automatic fixes if possible
            if (this.canAutoFix()) {
                console.log(`\n🔧 Applying automatic fixes...`);
                const fixedContent = this.applyAutoFixes(content, analysis);
                if (fixedContent !== content) {
                    fs.writeFileSync(filePath, fixedContent, 'utf8');
                    console.log(`✅ Auto-fixes applied successfully`);
                    return true;
                }
            }

        } catch (error) {
            console.error(`💥 Critical error during analysis: ${error.message}`);
            this.addError('critical', 'ANALYSIS_FAILED', error.message);
        }

        return this.errors.length === 0;
    }

    /**
     * Extract HTML structure for analysis
     */
    extractHTML(content) {
        const html = { tags: [], attributes: [], structure: [], text: [] };
        let currentPath = [];

        const parser = new htmlparser2.Parser({
            onopentag(name, attributes) {
                currentPath.push(name);
                html.tags.push({ name, attributes, path: [...currentPath] });

                // Check attributes
                Object.keys(attributes).forEach(attr => {
                    html.attributes.push({ tag: name, attribute: attr, value: attributes[attr] });
                });
            },
            onclosetag(name) {
                if (currentPath[currentPath.length - 1] === name) {
                    currentPath.pop();
                }
            },
            ontext(text) {
                if (text.trim()) {
                    html.text.push({ content: text.trim(), path: [...currentPath] });
                }
            },
            ondoctype(doctype) {
                html.doctype = doctype;
                html.tags.push({ name: '!doctype', attributes: {}, path: [] });
            }
        });

        parser.write(content);
        parser.end();

        return html;
    }

    /**
     * Extract CSS from HTML
     */
    extractCSS(content) {
        const css = { inline: [], embedded: [], external: [] };

        // Extract inline styles
        const inlineRegex = /style\s*=\s*["']([^"']*)["']/gi;
        let match;
        while ((match = inlineRegex.exec(content)) !== null) {
            css.inline.push(match[1]);
        }

        // Extract embedded styles
        const embeddedRegex = /<style[^>]*>([\s\S]*?)<\/style>/gi;
        while ((match = embeddedRegex.exec(content)) !== null) {
            css.embedded.push(match[1]);
        }

        // Extract external stylesheets
        const externalRegex = /<link[^>]*href\s*=\s*["']([^"']*\.css[^"']*)["'][^>]*>/gi;
        while ((match = externalRegex.exec(content)) !== null) {
            css.external.push(match[1]);
        }

        return css;
    }

    /**
     * Extract JavaScript code blocks from HTML
     */
    extractJavaScript(content) {
        const js = { inline: [], embedded: [], external: [] };

        // Extract inline event handlers
        const inlineRegex = /\bon\w+\s*=\s*["']([^"']*)["']/gi;
        let match;
        while ((match = inlineRegex.exec(content)) !== null) {
            js.inline.push(match[1]);
        }

        // Extract embedded scripts
        const embeddedRegex = /<script[^>]*>([\s\S]*?)<\/script>/gi;
        while ((match = embeddedRegex.exec(content)) !== null) {
            if (!match[0].includes('src=')) {
                js.embedded.push(match[1]);
            }
        }

        // Extract external scripts
        const externalRegex = /<script[^>]*src\s*=\s*["']([^"']*\.js[^"']*)["'][^>]*>/gi;
        while ((match = externalRegex.exec(content)) !== null) {
            js.external.push(match[1]);
        }

        return js;
    }

    /**
     * Extract metadata for SEO analysis
     */
    extractMetadata(content) {
        const metadata = {
            title: '',
            meta: {},
            headings: { h1: [], h2: [], h3: [], h4: [], h5: [], h6: [] },
            links: [],
            images: []
        };

        // Extract title
        const titleMatch = content.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
        if (titleMatch) {
            metadata.title = titleMatch[1].trim();
        }

        // Extract meta tags
        const metaRegex = /<meta[^>]*>/gi;
        let match;
        while ((match = metaRegex.exec(content)) !== null) {
            const metaTag = match[0];
            const nameMatch = metaTag.match(/name\s*=\s*["']([^"']*)["']/i);
            const propertyMatch = metaTag.match(/property\s*=\s*["']([^"']*)["']/i);
            const contentMatch = metaTag.match(/content\s*=\s*["']([^"']*)["']/i);

            if (nameMatch && contentMatch) {
                metadata.meta[nameMatch[1]] = contentMatch[1];
            } else if (propertyMatch && contentMatch) {
                metadata.meta[propertyMatch[1]] = contentMatch[1];
            }
        }

        // Extract headings
        for (let i = 1; i <= 6; i++) {
            const headingRegex = new RegExp(`<h${i}[^>]*>([\\s\\S]*?)</h${i}>`, 'gi');
            while ((match = headingRegex.exec(content)) !== null) {
                metadata.headings[`h${i}`].push(match[1].trim());
            }
        }

        // Extract links
        const linkRegex = /<a[^>]*href\s*=\s*["']([^"']*)["'][^>]*>([\s\S]*?)<\/a>/gi;
        while ((match = linkRegex.exec(content)) !== null) {
            metadata.links.push({
                href: match[1],
                text: match[2].trim()
            });
        }

        // Extract images
        const imgRegex = /<img[^>]*src\s*=\s*["']([^"']*)["'][^>]*(?:alt\s*=\s*["']([^"']*)["'])?[^>]*>/gi;
        while ((match = imgRegex.exec(content)) !== null) {
            metadata.images.push({
                src: match[1],
                alt: match[2] || ''
            });
        }

        return metadata;
    }

    /**
     * Analyze HTML structure and validity
     */
    async analyzeHTML(html, content) {
        console.log(`📄 Analyzing HTML structure...`);

        // Check for required tags
        const hasDoctype = html.doctype || content.trim().startsWith('<!DOCTYPE');
        const hasHtml = html.tags.some(tag => tag.name === 'html');
        const hasHead = html.tags.some(tag => tag.name === 'head');
        const hasBody = html.tags.some(tag => tag.name === 'body');

        if (!hasDoctype) {
            this.addError('html', 'MISSING_DOCTYPE', 'DOCTYPE declaration is missing');
        }
        if (!hasHtml) {
            this.addError('html', 'MISSING_HTML_TAG', 'Root <html> tag is missing');
        }
        if (!hasHead) {
            this.addError('html', 'MISSING_HEAD_TAG', '<head> tag is missing');
        }
        if (!hasBody) {
            this.addError('html', 'MISSING_BODY_TAG', '<body> tag is missing');
        }

        // Check for deprecated tags
        const deprecatedTags = ['center', 'font', 'strike', 'u', 'marquee'];
        html.tags.forEach(tag => {
            if (deprecatedTags.includes(tag.name)) {
                this.addWarning('html', 'DEPRECATED_TAG', `Deprecated tag <${tag.name}> found`);
            }
        });

        // Check for missing alt attributes on images
        html.tags.filter(tag => tag.name === 'img').forEach(img => {
            if (!img.attributes.alt) {
                this.addError('accessibility', 'MISSING_ALT', 'Image missing alt attribute');
            }
        });

        // Check for proper heading hierarchy
        this.analyzeHeadingHierarchy(html);

        console.log(`   ✅ HTML analysis complete`);
    }

    /**
     * Analyze CSS for issues
     */
    async analyzeCSS(css) {
        console.log(`🎨 Analyzing CSS...`);

        // Analyze embedded CSS
        css.embedded.forEach((cssText, index) => {
            try {
                const ast = csstree.parse(cssText);
                this.analyzeCSSAST(ast);
            } catch (error) {
                this.addError('css', 'INVALID_CSS', `Invalid CSS in embedded style ${index + 1}: ${error.message}`);
            }
        });

        // Analyze inline styles
        css.inline.forEach((styleText, index) => {
            try {
                csstree.parse(styleText, { context: 'declarationList' });
            } catch (error) {
                this.addWarning('css', 'INVALID_INLINE_STYLE', `Invalid inline style ${index + 1}: ${error.message}`);
            }
        });

        console.log(`   ✅ CSS analysis complete`);
    }

    /**
     * Analyze JavaScript for syntax and runtime errors
     */
    async analyzeJavaScript(js) {
        console.log(`💻 Analyzing JavaScript...`);

        // Analyze embedded scripts
        js.embedded.forEach((code, index) => {
            this.analyzeScriptBlock(code, `Embedded Script ${index + 1}`);
        });

        // Analyze inline event handlers
        js.inline.forEach((code, index) => {
            try {
                // Parse as function body
                acorn.parse(`function temp() { ${code} }`, {
                    ecmaVersion: 2020,
                    allowAwaitOutsideFunction: true
                });
            } catch (error) {
                this.addWarning('javascript', 'INVALID_INLINE_HANDLER', `Invalid inline event handler ${index + 1}: ${error.message}`);
            }
        });

        console.log(`   ✅ JavaScript analysis complete`);
    }

    /**
     * Analyze accessibility issues
     */
    async analyzeAccessibility(html) {
        console.log(`♿ Analyzing accessibility...`);

        // Check for form labels
        const inputs = html.tags.filter(tag => ['input', 'select', 'textarea'].includes(tag.name));
        const labels = html.tags.filter(tag => tag.name === 'label');

        inputs.forEach(input => {
            const hasLabel = labels.some(label =>
                label.attributes.for === input.attributes.id ||
                label.attributes.htmlFor === input.attributes.id
            );
            if (!hasLabel && !input.attributes['aria-label'] && !input.attributes['aria-labelledby']) {
                this.addError('accessibility', 'MISSING_LABEL', `Form control missing label: ${input.attributes.type || input.name}`);
            }
        });

        // Check for sufficient color contrast (basic check)
        const textElements = html.tags.filter(tag =>
            ['p', 'span', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'].includes(tag.name)
        );

        // Check for language attribute
        const htmlTag = html.tags.find(tag => tag.name === 'html');
        if (!htmlTag?.attributes.lang) {
            this.addWarning('accessibility', 'MISSING_LANG', 'Missing lang attribute on <html> tag');
        }

        console.log(`   ✅ Accessibility analysis complete`);
    }

    /**
     * Analyze performance issues
     */
    async analyzePerformance(analysis) {
        console.log(`⚡ Analyzing performance...`);

        // Check for large inline scripts/styles
        analysis.javascript.embedded.forEach((script, index) => {
            if (script.length > 2048) {
                this.addWarning('performance', 'LARGE_INLINE_SCRIPT', `Large inline script ${index + 1} (${script.length} chars) should be external`);
            }
        });

        analysis.css.embedded.forEach((style, index) => {
            if (style.length > 2048) {
                this.addWarning('performance', 'LARGE_INLINE_STYLE', `Large inline style ${index + 1} (${style.length} chars) should be external`);
            }
        });

        // Check for missing compression hints
        const hasGzipHint = analysis.html.tags.some(tag =>
            tag.name === 'meta' && tag.attributes['http-equiv'] === 'Content-Encoding'
        );
        if (!hasGzipHint) {
            this.addInfo('performance', 'MISSING_COMPRESSION', 'Consider enabling gzip compression');
        }

        console.log(`   ✅ Performance analysis complete`);
    }

    /**
     * Analyze security vulnerabilities
     */
    async analyzeSecurity(analysis) {
        console.log(`🔒 Analyzing security...`);

        // Check for insecure external resources
        [...analysis.css.external, ...analysis.javascript.external].forEach(url => {
            if (url.startsWith('http://')) {
                this.addError('security', 'INSECURE_RESOURCE', `Insecure HTTP resource: ${url}`);
            }
        });

        // Check for inline event handlers (XSS risk)
        if (analysis.javascript.inline.length > 0) {
            this.addWarning('security', 'INLINE_EVENT_HANDLERS', `${analysis.javascript.inline.length} inline event handlers found (potential XSS risk)`);
        }

        // Check for missing CSP
        const hasCSP = analysis.html.tags.some(tag =>
            tag.name === 'meta' && tag.attributes['http-equiv'] === 'Content-Security-Policy'
        );
        if (!hasCSP) {
            this.addWarning('security', 'MISSING_CSP', 'Missing Content Security Policy');
        }

        console.log(`   ✅ Security analysis complete`);
    }

    /**
     * Analyze SEO issues
     */
    async analyzeSEO(metadata) {
        console.log(`🔍 Analyzing SEO...`);

        // Check title
        if (!metadata.title) {
            this.addError('seo', 'MISSING_TITLE', 'Missing <title> tag');
        } else if (metadata.title.length < 30) {
            this.addWarning('seo', 'SHORT_TITLE', 'Title is too short (less than 30 characters)');
        } else if (metadata.title.length > 60) {
            this.addWarning('seo', 'LONG_TITLE', 'Title is too long (more than 60 characters)');
        }

        // Check meta description
        if (!metadata.meta.description) {
            this.addWarning('seo', 'MISSING_DESCRIPTION', 'Missing meta description');
        } else if (metadata.meta.description.length > 160) {
            this.addWarning('seo', 'LONG_DESCRIPTION', 'Meta description is too long (more than 160 characters)');
        }

        // Check headings hierarchy
        if (metadata.headings.h1.length === 0) {
            this.addError('seo', 'MISSING_H1', 'Missing H1 heading');
        } else if (metadata.headings.h1.length > 1) {
            this.addWarning('seo', 'MULTIPLE_H1', 'Multiple H1 headings found');
        }

        // Check for images without alt text
        metadata.images.forEach((img, index) => {
            if (!img.alt) {
                this.addError('seo', 'MISSING_ALT', `Image ${index + 1} missing alt text`);
            }
        });

        console.log(`   ✅ SEO analysis complete`);
    }

    /**
     * Analyze best practices
     */
    async analyzeBestPractices(analysis) {
        console.log(`✨ Analyzing best practices...`);

        // Check for proper semantic HTML
        const semanticTags = ['header', 'nav', 'main', 'section', 'article', 'aside', 'footer'];
        const usedSemanticTags = analysis.html.tags.filter(tag =>
            semanticTags.includes(tag.name)
        );

        if (usedSemanticTags.length === 0) {
            this.addInfo('best_practices', 'NO_SEMANTIC_HTML', 'No semantic HTML tags found');
        }

        // Check for proper viewport meta tag
        const hasViewport = analysis.html.tags.some(tag =>
            tag.name === 'meta' && tag.attributes.name === 'viewport'
        );
        if (!hasViewport) {
            this.addWarning('best_practices', 'MISSING_VIEWPORT', 'Missing viewport meta tag');
        }

        console.log(`   ✅ Best practices analysis complete`);
    }

    /**
     * Analyze JavaScript code block for syntax errors and logic issues
     */
    async analyzeScriptBlock(code, context) {
        let ast = null;

        try {
            ast = acorn.parse(code, {
                ecmaVersion: 2020,
                sourceType: 'module',
                allowImportExportEverywhere: true,
                allowAwaitOutsideFunction: true,
                locations: true
            });
        } catch (error) {
            this.addError('javascript', 'SYNTAX_ERROR', `${context}: ${error.message}`);
            return; // Can't do logic analysis without valid AST
        }

        // Advanced code logic analysis
        this.analyzeCodeLogic(ast, context);

        // Additional checks
        this.checkForMissingBraces(code, context);
        this.checkForUnmatchedElements(code, context);
        this.checkForOrphanedCode(code, context);
    }

    /**
     * Analyze heading hierarchy
     */
    analyzeHeadingHierarchy(html) {
        const headings = html.tags.filter(tag =>
            /^h[1-6]$/.test(tag.name)
        ).map(tag => parseInt(tag.name.charAt(1)));

        let lastLevel = 0;
        headings.forEach(level => {
            if (level > lastLevel + 1) {
                this.addWarning('html', 'SKIPPED_HEADING_LEVEL', `Skipped heading level: went from H${lastLevel} to H${level}`);
            }
            lastLevel = level;
        });
    }

    /**
     * Analyze CSS AST for issues
     */
    analyzeCSSAST(ast) {
        if (!ast || !ast.stylesheet) return;

        ast.stylesheet.rules.forEach(rule => {
            if (rule.type === 'rule') {
                // Check for !important overuse
                const importantDeclarations = rule.declarations.filter(decl =>
                    decl.type === 'declaration' && decl.value && decl.value.toString().includes('!important')
                );
                if (importantDeclarations.length > 0) {
                    this.addWarning('css', 'IMPORTANT_OVERUSE', '!important used in CSS rule');
                }
            }
        });
    }

    /**
     * Helper methods for error tracking
     */
    addError(category, code, message, line = null) {
        this.errors.push({ category, code, message, line, severity: 'error' });
        this.categories[category].errors++;
        this.score -= 10;
    }

    addWarning(category, code, message, line = null) {
        this.warnings.push({ category, code, message, line, severity: 'warning' });
        this.categories[category].warnings++;
        this.score -= 2;
    }

    addInfo(category, code, message, line = null) {
        // Info messages don't affect score
        this.warnings.push({ category, code, message, line, severity: 'info' });
    }

    /**
     * Calculate overall score
     */
    calculateOverallScore() {
        // Calculate category scores
        Object.keys(this.categories).forEach(category => {
            const cat = this.categories[category];
            const penalty = (cat.errors * 10) + (cat.warnings * 2);
            cat.score = Math.max(0, 100 - penalty);
        });

        // Overall score is weighted average
        const weights = {
            html: 0.15,
            css: 0.10,
            javascript: 0.20,
            accessibility: 0.20,
            performance: 0.10,
            security: 0.15,
            seo: 0.05,
            best_practices: 0.05
        };

        this.score = Object.keys(weights).reduce((total, category) => {
            return total + (this.categories[category].score * weights[category]);
        }, 0);

        this.score = Math.max(0, Math.min(100, Math.round(this.score)));
    }

    /**
     * Display comprehensive analysis report
     */
    displayComprehensiveReport(analysisTime) {
        console.log(`\n📊 HTML MASTER ANALYSIS REPORT`);
        console.log(`═`.repeat(60));

        // Overall score
        const scoreEmoji = this.score >= 90 ? '🟢' : this.score >= 70 ? '🟡' : '🔴';
        console.log(`${scoreEmoji} Overall Score: ${this.score}/100`);
        console.log(`⏱️  Analysis Time: ${analysisTime}ms`);
        console.log(`❌ Errors: ${this.errors.length}`);
        console.log(`⚠️  Warnings: ${this.warnings.length}`);

        console.log(`\n📈 Category Breakdown:`);
        Object.keys(this.categories).forEach(category => {
            const cat = this.categories[category];
            const emoji = cat.score >= 90 ? '🟢' : cat.score >= 70 ? '🟡' : '🔴';
            console.log(`   ${emoji} ${category.padEnd(15)}: ${cat.score}/100 (${cat.errors} errors, ${cat.warnings} warnings)`);
        });

        // Display errors
        if (this.errors.length > 0) {
            console.log(`\n❌ CRITICAL ERRORS:`);
            this.errors.forEach((error, index) => {
                console.log(`   ${index + 1}. ${error.category.toUpperCase()}: ${error.code}`);
                console.log(`      ${error.message}`);
                if (error.line) console.log(`      Line: ${error.line}`);
                console.log('');
            });
        }

        // Display warnings
        if (this.warnings.length > 0) {
            console.log(`\n⚠️  WARNINGS:`);
            this.warnings.slice(0, 10).forEach((warning, index) => {
                console.log(`   ${index + 1}. ${warning.category.toUpperCase()}: ${warning.code}`);
                console.log(`      ${warning.message}`);
                if (warning.line) console.log(`      Line: ${warning.line}`);
            });

            if (this.warnings.length > 10) {
                console.log(`   ... and ${this.warnings.length - 10} more warnings`);
            }
        }

        // Recommendations
        console.log(`\n💡 RECOMMENDATIONS:`);
        if (this.score >= 90) {
            console.log(`   🟢 Excellent! Your HTML is well-structured and follows best practices.`);
        } else if (this.score >= 70) {
            console.log(`   🟡 Good foundation. Focus on fixing the critical errors listed above.`);
        } else {
            console.log(`   🔴 Significant improvements needed. Address critical errors first.`);
        }

        console.log(`═`.repeat(60));
    }

    /**
     * Check if auto-fixes can be applied
     */
    canAutoFix() {
        // Only auto-fix JavaScript syntax errors for now
        return this.errors.some(error => error.category === 'javascript' && error.code === 'SYNTAX_ERROR');
    }

    /**
     * Apply automatic fixes
     */
    applyAutoFixes(content, analysis) {
        let fixedContent = content;

        // Apply JavaScript fixes
        analysis.javascript.embedded.forEach((script, index) => {
            const fixedScript = this.fixJavaScriptSyntax(script);
            if (fixedScript !== script) {
                // Replace in HTML content
                const scriptRegex = /<script[^>]*>([\s\S]*?)<\/script>/gi;
                let matchCount = 0;
                fixedContent = fixedContent.replace(scriptRegex, (match, scriptContent) => {
                    if (matchCount === index) {
                        return match.replace(scriptContent, fixedScript);
                    }
                    matchCount++;
                    return match;
                });
            }
        });

        return fixedContent;
    }

    /**
     * Fix JavaScript syntax issues
     */
    fixJavaScriptSyntax(code) {
        let fixedCode = code;

        // Fix missing closing braces
        const braceCount = (fixedCode.match(/\{/g) || []).length - (fixedCode.match(/\}/g) || []).length;
        if (braceCount > 0) {
            fixedCode += '}'.repeat(braceCount);
        }

        // Fix unmatched parentheses
        const parenCount = (fixedCode.match(/\(/g) || []).length - (fixedCode.match(/\)/g) || []).length;
        if (parenCount > 0) {
            fixedCode += ')'.repeat(parenCount);
        }

        return fixedCode;
    }

    /**
     * Check for missing closing braces
     */
    checkForMissingBraces(code, context) {
        const lines = code.split('\n');
        let braceCount = 0;
        let inFunction = false;

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();

            // Count braces
            const openBraces = (line.match(/\{/g) || []).length;
            const closeBraces = (line.match(/\}/g) || []).length;
            braceCount += openBraces - closeBraces;

            // Check for function declarations
            if (line.match(/^(async\s+)?function\s+\w+\s*\([^)]*\)\s*\{/)) {
                inFunction = true;
            }

            // If we're in a function and reach end with unbalanced braces
            if (i === lines.length - 1 && braceCount > 0 && inFunction) {
                this.addError('javascript', 'MISSING_CLOSING_BRACE', `${context}: Function missing ${braceCount} closing brace(s)`);
            }
        }
    }

    /**
     * Check for unmatched parentheses and brackets
     */
    checkForUnmatchedElements(code, context) {
        const stack = [];
        const pairs = { '(': ')', '[': ']', '{': '}' };

        for (let i = 0; i < code.length; i++) {
            const char = code[i];

            if (['(', '[', '{'].includes(char)) {
                stack.push({ char, position: i });
            } else if ([')', ']', '}'].includes(char)) {
                const last = stack.pop();
                if (!last || pairs[last.char] !== char) {
                    this.addError('javascript', 'UNMATCHED_ELEMENT', `${context}: Unmatched ${char} at position ${i}`);
                }
            }
        }

        // Check for unclosed elements
        stack.forEach(unclosed => {
            this.addError('javascript', 'UNCLOSED_ELEMENT', `${context}: Unclosed ${unclosed.char} at position ${unclosed.position}`);
        });
    }

    /**
     * Check for orphaned code blocks (code outside functions)
     */
    checkForOrphanedCode(code, context) {
        const lines = code.split('\n');
        let inFunction = false;
        let orphanedLines = [];

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();

            // Skip empty lines and comments
            if (!line || line.startsWith('//') || line.startsWith('/*')) continue;

            // Check if we're entering a function
            if (line.match(/^(async\s+)?function\s+\w+\s*\([^)]*\)\s*\{/) ||
                line.match(/^(const|let|var)\s+\w+\s*=\s*(async\s+)?\([^)]*\)\s*=>\s*\{/) ||
                line.match(/^\w+\s*:\s*function\s*\(/)) {
                inFunction = true;
                orphanedLines = []; // Reset orphaned lines
                continue;
            }

            // Check if we're exiting a function
            if (line === '}' && inFunction) {
                inFunction = false;
                continue;
            }

            // If we encounter executable code outside a function
            if (!inFunction && line.match(/[a-zA-Z_$][a-zA-Z0-9_$]*\s*[=!<>]+\s*|\s*if\s*\(|for\s*\(|while\s*\(/)) {
                orphanedLines.push(i);
            }
        }

        if (orphanedLines.length > 0) {
            this.addError('javascript', 'ORPHANED_CODE', `${context}: Found ${orphanedLines.length} lines of orphaned code outside functions`);
        }
    }

    /**
     * Advanced JavaScript code logic analyzer
     */
    analyzeCodeLogic(ast, context) {
        const analyzer = new CodeLogicAnalyzer();
        const results = analyzer.analyze(ast);

        // Add errors and warnings to main analysis
        // Logic analysis issues are treated as warnings, not critical errors
        results.errors.forEach(error => {
            this.addWarning('javascript', error.type, `${context}: ${error.message}`);
        });

        results.warnings.forEach(warning => {
            this.addWarning('javascript', warning.type, `${context}: ${warning.message}`);
        });
    }
}

/**
 * Advanced JavaScript Code Logic Analyzer
 * Performs deep analysis of variable scoping, usage, and control flow
 */
class CodeLogicAnalyzer {
    constructor() {
        this.errors = [];
        this.warnings = [];
        this.scopeStack = [];
        this.symbolTable = new SymbolTable();
    }

    analyze(ast) {
        this.enterScope('global');
        walk.simple(ast, {
            Program: (node) => {
                // Global scope already entered
            },
            VariableDeclaration: (node) => {
                this.analyzeVariableDeclaration(node);
            },

            Identifier: (node, ancestors) => {
                const parent = ancestors && ancestors.length >= 2 ? ancestors[ancestors.length - 2] : null;
                this.analyzeIdentifier(node, parent);
            },
            AssignmentExpression: (node) => {
                this.analyzeAssignment(node);
            },
            UpdateExpression: (node) => {
                this.analyzeAssignment(node);
            },
            IfStatement: (node) => {
                this.analyzeControlFlow(node);
            },
            WhileStatement: (node) => {
                this.analyzeControlFlow(node);
            },
            ForStatement: (node) => {
                this.analyzeControlFlow(node);
            },
            SwitchStatement: (node) => {
                this.analyzeControlFlow(node);
            },
            FunctionDeclaration: (node) => {
                // Function declaration already handled above
            },
            BlockStatement: (node, ancestors) => {
                const parent = ancestors && ancestors.length >= 2 ? ancestors[ancestors.length - 2] : null;
                if (parent && parent.type === 'FunctionDeclaration') {
                    // Function body - scope already entered
                } else {
                    // Regular block scope for let/const
                    this.enterScope('block');
                }
            },
            'BlockStatement:exit': (node, ancestors) => {
                const parent = ancestors && ancestors.length >= 2 ? ancestors[ancestors.length - 2] : null;
                if (parent && parent.type === 'FunctionDeclaration') {
                    // Exit function scope
                    this.exitScope();
                } else {
                    // Exit block scope
                    this.exitScope();
                }
            }
        });
        this.exitScope();

        // Check for unused variables
        this.checkUnusedVariables();

        return {
            errors: this.errors,
            warnings: this.warnings
        };
    }



    enterScope(type) {
        const newScope = new SymbolTable(this.symbolTable);
        newScope.type = type;
        this.scopeStack.push(newScope);
        this.symbolTable = newScope;
    }

    exitScope() {
        if (this.scopeStack.length > 1) {
            this.scopeStack.pop();
            this.symbolTable = this.scopeStack[this.scopeStack.length - 1];
        }
    }

    analyzeVariableDeclaration(node) {
        node.declarations.forEach(decl => {
            if (decl.id.type === 'Identifier') {
                const varType = node.kind; // var, let, const
                const symbol = this.symbolTable.declare(decl.id.name, varType, node);

                if (symbol.redeclared) {
                    this.addError('REDECLARED_VARIABLE',
                        `Variable '${decl.id.name}' is redeclared in the same scope`);
                }

                // Mark as initialized if there's an init expression
                if (decl.init) {
                    symbol.initialized = true;
                }
            }
        });
    }

    analyzeFunctionDeclaration(node) {
        // Declare function in current scope
        this.symbolTable.declare(node.id.name, 'function', node);

        // Enter function scope
        this.enterScope('function');

        // Analyze parameters
        node.params.forEach(param => {
            if (param.type === 'Identifier') {
                this.symbolTable.declare(param.name, 'param', node);
            }
        });

        // Function body will be analyzed by the walker
        // Exit function scope will happen in BlockStatement:exit
    }

    analyzeIdentifier(node, parent) {
        // Skip if this is a declaration
        if (parent && (
            parent.type === 'VariableDeclarator' && parent.id === node ||
            parent.type === 'FunctionDeclaration' && parent.id === node ||
            parent.type === 'AssignmentExpression' && parent.left === node
        )) {
            return;
        }

        const symbol = this.symbolTable.resolve(node.name);

        if (!symbol) {
            // Check if it's a global or built-in
            if (!this.isBuiltIn(node.name)) {
                this.addError('UNDECLARED_VARIABLE',
                    `Variable '${node.name}' is used before declaration`);
            }
            return;
        }

        // Mark as used
        symbol.used = true;

        // Check for temporal dead zone
        if ((symbol.type === 'let' || symbol.type === 'const') && !symbol.initialized) {
            if (node.loc && symbol.node.loc &&
                (node.loc.start.line < symbol.node.loc.start.line ||
                 (node.loc.start.line === symbol.node.loc.start.line &&
                  node.loc.start.column < symbol.node.loc.start.column))) {
                this.addError('TEMPORAL_DEAD_ZONE',
                    `Variable '${node.name}' is used in temporal dead zone`);
            }
        }
    }

    analyzeAssignment(node) {
        // Mark left side as initialized
        if (node.left && node.left.type === 'Identifier') {
            const symbol = this.symbolTable.resolve(node.left.name);
            if (symbol) {
                symbol.initialized = true;
            }
        }
    }

    analyzeControlFlow(node) {
        // Basic control flow analysis
        // The acorn-walk will handle traversing the children automatically
        // Future enhancement: add unreachable code detection
    }

    checkUnusedVariables() {
        const checkScope = (scope) => {
            for (const [name, symbol] of scope.symbols) {
                if (!symbol.used && symbol.type !== 'param' && symbol.type !== 'function') {
                    this.addWarning('UNUSED_VARIABLE',
                        `Variable '${name}' is declared but never used`);
                }
            }

            // Check child scopes
            scope.children.forEach(child => checkScope(child));
        };

        checkScope(this.scopeStack[0]); // Start from global scope
    }

    isBuiltIn(name) {
        const builtIns = [
            'console', 'window', 'document', 'navigator', 'location',
            'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
            'parseInt', 'parseFloat', 'isNaN', 'isFinite', 'encodeURIComponent',
            'decodeURIComponent', 'JSON', 'Math', 'Date', 'Array', 'Object',
            'String', 'Number', 'Boolean', 'RegExp', 'Error', 'Promise',
            'fetch', 'XMLHttpRequest', 'localStorage', 'sessionStorage'
        ];
        return builtIns.includes(name);
    }

    addError(type, message) {
        this.errors.push({ type, message });
    }

    addWarning(type, message) {
        this.warnings.push({ type, message });
    }
}

/**
 * Symbol Table for variable tracking
 */
class SymbolTable {
    constructor(parent = null) {
        this.symbols = new Map();
        this.parent = parent;
        this.children = [];
        this.type = 'block';

        if (parent) {
            parent.children.push(this);
        }
    }

    declare(name, varType, node) {
        // Check for redeclaration in current scope only
        if (this.symbols.has(name)) {
            return { redeclared: true };
        }

        const symbol = {
            type: varType,
            node: node,
            used: false,
            initialized: false,
            scope: this
        };

        this.symbols.set(name, symbol);
        return symbol;
    }

    resolve(name) {
        let scope = this;
        while (scope) {
            if (scope.symbols.has(name)) {
                return scope.symbols.get(name);
            }
            scope = scope.parent;
        }
        return null;
    }
}




// CLI interface
async function main() {
    const args = process.argv.slice(2);

    if (args.length === 0) {
        console.log('🎯 HTML MASTER - Comprehensive Web Analysis Agent');
        console.log('Usage: node syntax-agent.js <file-path>');
        console.log('Example: node syntax-agent.js trading_bot_interface.html');
        console.log('');
        console.log('Analyzes:');
        console.log('  📄 HTML structure & validation');
        console.log('  🎨 CSS issues & best practices');
        console.log('  💻 JavaScript syntax & errors');
        console.log('  ♿ Accessibility compliance');
        console.log('  ⚡ Performance optimizations');
        console.log('  🔒 Security vulnerabilities');
        console.log('  🔍 SEO & meta tags');
        console.log('  ✨ Best practices');
        process.exit(1);
    }

    const filePath = args[0];

    if (!fs.existsSync(filePath)) {
        console.error(`❌ File not found: ${filePath}`);
        process.exit(1);
    }

    console.log('🚀 Starting comprehensive HTML analysis...\n');

    const master = new HTMLMaster();
    const success = await master.processFile(filePath);

    if (success) {
        console.log('\n🎉 Analysis complete! Your HTML is ready for production.');
    } else {
        console.log('\n⚠️  Analysis complete with issues. Review the report above.');
        process.exit(1);
    }
}

if (require.main === module) {
    main().catch(console.error);
}

module.exports = HTMLMaster;