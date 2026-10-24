# Actor — よく使うコマンド
PY ?= python3
PORT ?= 8080

.PHONY: help chat serve bot rooms tools attach test doctor skills export clean ui-test

ROOM ?= actor-x
SKILLS ?= mika

help:
	@echo "make chat     対話を開始"
	@echo "make bot      常駐ボットを起動 (ROOM=$(ROOM) SKILLS=$(SKILLS))"
	@echo "make rooms    ルームの中身を表示 (ROOM=$(ROOM))"
	@echo "make tools    使える道具（function calling）の一覧"
	@echo "make attach   画像を添えて1回だけ聞く (FILE=./図.png)"
	@echo "make serve     Web UI / API を起動 (PORT=$(PORT))"
	@echo "make test      テスト（依存ゼロ）"
	@echo "make doctor    環境チェック"
	@echo "make skills    スキル一覧"
	@echo "make export    全スキルを ZIP 化"
	@echo "make ui-test   Web UI のスモークテスト（要: npm i jsdom）"

chat:
	$(PY) -m actor

serve:
	$(PY) -m actor serve --port $(PORT) --skills-dir skills --skills-dir examples/mika

bot:
	$(PY) -m actor bot --room $(ROOM) --skills $(SKILLS) --name ミカ --stream \
		--skills-dir skills --skills-dir examples/mika

rooms:
	$(PY) -m actor rooms show $(ROOM) --skills-dir skills --skills-dir examples/mika

tools:
	$(PY) -m actor tools --skills-dir skills --skills-dir examples/mika

# 例: make attach FILE=./design.png PROMPT="この画面の課題を3つ挙げて"
PROMPT ?= この画像を説明して
FILE ?= ./examples/mika/SKILL.md
attach:
	$(PY) -m actor --attach $(FILE) "$(PROMPT)"

test:
	$(PY) -m unittest discover -s tests -v

doctor:
	$(PY) -m actor doctor --check-api

skills:
	$(PY) -m actor skills list

export:
	$(PY) -m actor skills export -o actor-skills.zip

ui-test:
	node tests/ui_smoke.mjs

clean:
	rm -rf .actor actor-skills.zip __pycache__ */__pycache__ .pytest_cache
