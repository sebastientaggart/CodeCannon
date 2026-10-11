# Makefile.agents.mk — Git workflow targets for agent-assisted development
#
# Include this in your project Makefile:
#   include Makefile.agents.mk
#
# Or copy these targets directly into your existing Makefile.
#
# Prerequisites: git, gh (GitHub CLI)
#
# Configuration:
#   INTEGRATION_BRANCH — the branch PRs target (default: development)
#   PRODUCTION_BRANCH  — the production branch (default: main)
#   STAGING_BRANCH     — optional staging/pre-prod branch (default: empty)
#   FEATURE_PREFIX     — prefix for feature branches (default: feature/)
#
# Workflow modes:
#   Two-branch (default):   feature → INTEGRATION_BRANCH → PRODUCTION_BRANCH
#   Three-branch:           feature → INTEGRATION_BRANCH → STAGING_BRANCH → PRODUCTION_BRANCH
#
#   Set STAGING_BRANCH to enable three-branch mode. When set:
#     - `promote` target becomes available (PR: integration → staging)
#     - STAGING_BRANCH is protected from direct feature work

INTEGRATION_BRANCH ?= development
PRODUCTION_BRANCH  ?= main
STAGING_BRANCH     ?=
FEATURE_PREFIX     ?= feature/

.PHONY: branch pr abandon merge promote

# Create a feature branch from the integration branch.
# Usage: make branch name=<feature-name>
branch:
ifndef name
	$(error Usage: make branch name=<feature-name>)
endif
	git fetch origin $(INTEGRATION_BRANCH)
	git checkout -b $(FEATURE_PREFIX)$(name) origin/$(INTEGRATION_BRANCH)

# Push the current branch and open a PR targeting the integration branch.
pr:
	@branch=$$(git rev-parse --abbrev-ref HEAD); \
	if [ "$$branch" = "$(PRODUCTION_BRANCH)" ] || [ "$$branch" = "$(INTEGRATION_BRANCH)" ]; then \
		echo "Error: cannot PR from $$branch. Use a feature branch."; exit 1; \
	fi; \
	if [ -n "$(STAGING_BRANCH)" ] && [ "$$branch" = "$(STAGING_BRANCH)" ]; then \
		echo "Error: cannot PR from $$branch. Use a feature branch."; exit 1; \
	fi; \
	git push -u origin "$$branch" && \
	gh pr create --base $(INTEGRATION_BRANCH) --fill

# Discard all changes and delete the current feature branch.
abandon:
	@branch=$$(git rev-parse --abbrev-ref HEAD); \
	if [ "$$branch" = "$(PRODUCTION_BRANCH)" ] || [ "$$branch" = "$(INTEGRATION_BRANCH)" ]; then \
		echo "Error: cannot abandon $$branch."; exit 1; \
	fi; \
	if [ -n "$(STAGING_BRANCH)" ] && [ "$$branch" = "$(STAGING_BRANCH)" ]; then \
		echo "Error: cannot abandon $$branch."; exit 1; \
	fi; \
	git checkout . && \
	git clean -fd && \
	git checkout $(INTEGRATION_BRANCH) && \
	git branch -D "$$branch" && \
	echo "Abandoned and deleted $$branch."

# Merge the current PR into the integration branch (after review).
# Each step before the reset exits on failure explicitly: an `&&` chain is cut
# by the `;` after an `if … fi`, so a failed merge or checkout would otherwise
# fall through to `git reset --hard` on the feature branch.
merge:
	@branch=$$(git rev-parse --abbrev-ref HEAD); \
	if [ "$$branch" = "$(PRODUCTION_BRANCH)" ] || [ "$$branch" = "$(INTEGRATION_BRANCH)" ]; then \
		echo "Error: cannot merge from $$branch. Use a feature branch."; exit 1; \
	fi; \
	if [ -n "$(STAGING_BRANCH)" ] && [ "$$branch" = "$(STAGING_BRANCH)" ]; then \
		echo "Error: cannot merge from $$branch. Use a feature branch."; exit 1; \
	fi; \
	pr_url=$$(gh pr view --json url -q .url 2>/dev/null); \
	if [ -z "$$pr_url" ]; then \
		echo "Error: no open PR found for $$branch."; exit 1; \
	fi; \
	pr_base=$$(gh pr view --json baseRefName -q .baseRefName); \
	if [ "$$pr_base" = "$(PRODUCTION_BRANCH)" ]; then \
		echo "Error: refusing to merge PR targeting $(PRODUCTION_BRANCH). Merge to $(PRODUCTION_BRANCH) manually."; exit 1; \
	fi; \
	gh pr merge --merge || exit 1; \
	git checkout $(INTEGRATION_BRANCH) || exit 1; \
	if ! git diff --quiet || ! git diff --cached --quiet; then \
		echo "Error: uncommitted changes on $(INTEGRATION_BRANCH); refusing to reset to origin. Resolve manually."; exit 1; \
	fi; \
	git fetch origin $(INTEGRATION_BRANCH) || exit 1; \
	ahead=$$(git rev-list --count origin/$(INTEGRATION_BRANCH)..HEAD); \
	if [ "$$ahead" != "0" ]; then \
		echo "Warning: discarding $$ahead local-only commit(s) on $(INTEGRATION_BRANCH) to match origin (recover via 'git reflog' within 90 days):"; \
		git log --oneline origin/$(INTEGRATION_BRANCH)..HEAD; \
	fi; \
	git reset --hard origin/$(INTEGRATION_BRANCH) && \
	echo "PR merged into $(INTEGRATION_BRANCH)."

# Promote integration branch to staging. Creates a PR: INTEGRATION_BRANCH → STAGING_BRANCH.
# Only available in three-branch mode (STAGING_BRANCH is set).
promote:
ifndef STAGING_BRANCH
	$(error promote requires STAGING_BRANCH to be set (three-branch mode))
endif
	gh pr create --base $(STAGING_BRANCH) --head $(INTEGRATION_BRANCH) \
		--title "Promote $(INTEGRATION_BRANCH) → $(STAGING_BRANCH)" --fill
