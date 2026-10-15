.PHONY: test release release-dry-run

export RELEASE_VERSION = $(VERSION)

test:
	bash tests/run.sh

release:
	@bash scripts/release.sh

release-dry-run:
	@bash scripts/release.sh --dry-run
