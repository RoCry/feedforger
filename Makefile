.PHONY: download_db sync-deploy

download_db:
	# Download the latest database from GitHub releases
	@curl -L https://github.com/RoCry/feedforger/releases/download/latest/feeds.sqlite -o cache/feeds.sqlite

sync-deploy:
	@test "$$(git branch --show-current)" = master || (echo "sync-deploy must run from master" >&2; exit 1)
	@test -z "$$(git status --porcelain)" || (echo "sync-deploy requires a clean worktree" >&2; exit 1)
	@test "$$(git rev-list --count master..deploy)" -eq 1 || (echo "deploy must contain exactly one commit on top of master" >&2; exit 1)
	git fetch origin master deploy
	git switch deploy
	git rebase master
	git push --force-with-lease origin deploy
	git switch master
