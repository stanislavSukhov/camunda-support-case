.PHONY: up down logs worker demo clean

up:            ## start Camunda 8 (Orchestration Cluster + Connectors) in Docker
	docker compose up -d
	@echo "Operate:  http://localhost:8080/operate   Tasklist: http://localhost:8080/tasklist   (demo / demo)"

down:          ## stop and remove containers (keeps volumes)
	docker compose down

clean:         ## stop and remove containers AND data volumes
	docker compose down -v
	rm -rf .data

logs:
	docker compose logs -f orchestration connectors

worker:        ## run the job worker in the foreground
	python3 worker/worker.py

demo:          ## deploy, run the four scenarios, print a summary
	python3 worker/run_demo.py
