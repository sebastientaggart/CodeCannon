# Makefile — CodeCannon project-specific targets
#
# Workflow targets (branch, pr, abandon, merge, promote) come from Makefile.agents.mk.
# This file adds CodeCannon-specific targets: sync, versioning, and deployment.

INTEGRATION_BRANCH = dev
include Makefile.agents.mk

.DEFAULT_GOAL := help
.PHONY: help check dev sync test bump-patch bump-minor bump-major set-version deploy-preview deploy-prod roll-call

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Sync"
	@echo "  check            Validate skill placeholders against config"
	@echo "  dev              Preview sync output (dry run)"
	@echo "  sync             Regenerate adapter output from skills/<group>/"
	@echo "  test             Run the sync engine test suite"
	@echo ""
	@echo "Git workflow (from Makefile.agents.mk)"
	@echo "  branch name=X    Create feature branch from $(INTEGRATION_BRANCH)"
	@echo "  pr               Push and open PR targeting $(INTEGRATION_BRANCH)"
	@echo "  merge            Merge current PR into $(INTEGRATION_BRANCH)"
	@echo "  abandon          Discard changes and delete current feature branch"
	@echo ""
	@echo "Versioning"
	@echo "  bump-patch       Bump patch (X.Y.Z+1), commit, and tag"
	@echo "  bump-minor       Bump minor (X.Y+1.0), commit, and tag"
	@echo "  bump-major       Bump major (X+1.0.0), commit, and tag"
	@echo "  set-version V=X  Set explicit version, commit, and tag"
	@echo ""
	@echo "Deployment"
	@echo "  deploy-preview   Push $(INTEGRATION_BRANCH) for preview/testing"
	@echo "  deploy-prod      Push $(PRODUCTION_BRANCH) with tags"
	@echo ""
	@echo "Community"
	@echo "  roll-call        Say hello — let us know you're using CodeCannon"

# Validate that all skill placeholders resolve against the config.
check:
	./sync.py --validate

# Preview what sync would generate without writing any files.
dev:
	./sync.py --validate
	./sync.py --dry-run

# Regenerate .claude/skills/ and other adapter output from the enabled skill group.
sync:
	./sync.py

# Run the sync engine test suite.
test:
	python3 -m unittest discover -s tests -v

# Bump the patch segment (X.Y.Z → X.Y.Z+1), commit, and tag.
bump-patch:
	@v=$$(cat VERSION); \
	major=$$(echo $$v | awk -F. '{print $$1}'); \
	minor=$$(echo $$v | awk -F. '{print $$2}'); \
	patch=$$(echo $$v | awk -F. '{print $$3}'); \
	new="$$major.$$minor.$$((patch + 1))"; \
	echo $$new > VERSION; \
	git add VERSION; \
	git commit -S -m "Bump version to $$new"; \
	git tag -s v$$new -m "v$$new"

# Bump the minor segment (X.Y.Z → X.Y+1.0), commit, and tag.
bump-minor:
	@v=$$(cat VERSION); \
	major=$$(echo $$v | awk -F. '{print $$1}'); \
	minor=$$(echo $$v | awk -F. '{print $$2}'); \
	new="$$major.$$((minor + 1)).0"; \
	echo $$new > VERSION; \
	git add VERSION; \
	git commit -S -m "Bump version to $$new"; \
	git tag -s v$$new -m "v$$new"

# Bump the major segment (X.Y.Z → X+1.0.0), commit, and tag.
bump-major:
	@v=$$(cat VERSION); \
	major=$$(echo $$v | awk -F. '{print $$1}'); \
	new="$$((major + 1)).0.0"; \
	echo $$new > VERSION; \
	git add VERSION; \
	git commit -S -m "Bump version to $$new"; \
	git tag -s v$$new -m "v$$new"

# Set an explicit version. Usage: make set-version V=1.2.3
set-version:
ifndef V
	$(error Usage: make set-version V=<version>)
endif
	echo $(V) > VERSION
	git add VERSION
	git commit -S -m "Bump version to $(V)"
	git tag -s v$(V) -m "v$(V)"

# Push the integration branch for preview/testing.
deploy-preview:
	git checkout $(INTEGRATION_BRANCH)
	git pull --rebase origin $(INTEGRATION_BRANCH)
	git push origin $(INTEGRATION_BRANCH)

# Publish a tagged release to production.
deploy-prod:
	git checkout $(PRODUCTION_BRANCH)
	git pull --rebase origin $(PRODUCTION_BRANCH)
	git fetch origin --tags --force
	git push origin $(PRODUCTION_BRANCH) --tags

# ── Community ───────────────────────────────────────────────────────────────

# Discussion node ID for the Roll Call registry (LightbridgeLab/CodeCannon #133).
ROLL_CALL_DISCUSSION_ID := D_kwDORls6r84Alpjt

# Post a voluntary check-in to the CodeCannon Installation Registry discussion.
roll-call:
	@# ── Preflight: gh must be installed and authenticated ──
	@command -v gh >/dev/null 2>&1 || { echo "Error: gh CLI is not installed. See https://cli.github.com"; exit 1; }
	@gh auth status >/dev/null 2>&1 || { echo "Error: not authenticated. Run 'gh auth login' first."; exit 1; }
	@# ── Gather metadata ──
	@repo=$$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null || echo "unknown"); \
	if [ -f CodeCannon/VERSION ]; then \
		version=$$(cat CodeCannon/VERSION); \
	elif [ -f VERSION ]; then \
		version=$$(cat VERSION); \
	else \
		version="unknown"; \
	fi; \
	if [ -f .codecannon.yaml ]; then \
		adapters=$$(grep -E '^\s*-\s+' .codecannon.yaml | grep -io 'claude\|cursor\|gemini\|codex\|agents' | sort -u | paste -sd ', ' -); \
	fi; \
	adapters=$${adapters:-none detected}; \
	date=$$(date +%Y-%m-%d); \
	body="📡 **$$repo** — v$$version — adapters: $$adapters — $$date"; \
	echo ""; \
	echo "  Roll Call — CodeCannon Installation Registry"; \
	echo "  ─────────────────────────────────────────────"; \
	echo "  Repo:      $$repo"; \
	echo "  Version:   v$$version"; \
	echo "  Adapters:  $$adapters"; \
	echo "  Date:      $$date"; \
	echo ""; \
	echo "  This will post a comment to the public CodeCannon discussion:"; \
	echo "  https://github.com/LightbridgeLab/CodeCannon/discussions/133"; \
	echo ""; \
	echo "  Your GitHub username and repo name will be visible."; \
	echo ""; \
	printf "  Continue? [y/N] "; \
	read -r confirm; \
	case "$$confirm" in \
		[yY]|[yY][eE][sS]) ;; \
		*) echo "  Cancelled."; exit 0;; \
	esac; \
	gh api graphql -f query="mutation { addDiscussionComment(input: {discussionId: \"$(ROLL_CALL_DISCUSSION_ID)\", body: \"$$body\"}) { comment { url } } }" --jq '.data.addDiscussionComment.comment.url' \
		&& echo "" && echo "  🎉 Thanks for checking in! You're helping make CodeCannon better." \
		|| { echo ""; echo "  Error: failed to post. Check your gh permissions and try again."; exit 1; }
