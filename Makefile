# Top-level entry points. `make test` runs everything quick (~1.5 min); `make full` adds fuzz + bootstrap.
.PHONY: all vm test full bootstrap ddc demo clean
all: vm
vm:
	$(MAKE) -C vm

test: vm
	python3 tests/test_vm.py
	python3 tests/test_sqasm.py
	tests/check_tier1.sh
	tests/run_corpus_interp.sh
	tests/run_corpus_compiled.sh
	scripts/tier3_diff.sh
	scripts/difftest.sh 40
	scripts/make_demo_trace.sh
	node tests/test_viewer_parse.js
	node tests/test_viewer_dom.js
	scripts/ddc.sh
	tests/test_ddc_detects_divergence.sh
	tests/test_superopt.sh
	@echo "ALL TESTS PASSED"

bootstrap: vm
	scripts/bootstrap.sh

ddc: vm
	scripts/ddc.sh

demo:
	scripts/make_demo_trace.sh

full: test bootstrap
	scripts/tier3_fuzz.sh 100 1
	scripts/difftest.sh 300 1

clean:
	$(MAKE) -C vm clean
	rm -rf build
