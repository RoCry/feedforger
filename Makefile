.PHONY: download_db

download_db:
	# Download the latest database from GitHub releases
	@curl -L https://github.com/RoCry/feedforger/releases/download/latest/feeds.sqlite -o cache/feeds.sqlite
