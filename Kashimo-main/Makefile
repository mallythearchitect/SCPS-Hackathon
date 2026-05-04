UV = /opt/homebrew/bin/uv
DEPS = --with requests --with python-dotenv --with pandas --with pandapower --with streamlit --with anthropic

dev:
	$(UV) run $(DEPS) streamlit run interface/frontend/app.py --server.headless true

fetch:
	$(UV) run $(DEPS) python3 src/fetch_load.py

baseline:
	$(UV) run $(DEPS) python3 src/baseline.py

simulate:
	$(UV) run $(DEPS) python3 src/run_simulation.py --steps 20 --top-spikes
