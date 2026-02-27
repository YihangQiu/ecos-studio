.PHONY: help setup init-submodules setup-pdk install-ecc demo-gcd demo-soc demo-retrosoc docker-build docker-verify docker-verify-gcd docker-verify-soc docker-verify-retrosoc docker-verify-all

PDK_ROOT ?= ./pdk/icsprout55-pdk
ECC_DIR ?= ./eda/ecc
ECC_CLI ?= $(ECC_DIR)\#cli
GCD_WS ?= ./ws/gcd
SOC_WS ?= ./ws/soc
RETROSOC_WS ?= ./ws/retrosoc
RETROSOC_FLIST ?= $(RETROSOC_WS)/retrosoc_ics55_minimal.f
RETROSOC_MINI_FLIST_DIR ?= $(CURDIR)/ip/retroSoC/rtl/mini/filelist
RETROSOC_PDK_FLIST ?= $(CURDIR)/ip/retroSoC/rtl/filelist/pdk_ics55.fl

help:
	@echo "Targets:"
	@echo "  make setup      - Init submodules, setup PDK assets, and build ECC CLI"
	@echo "  make demo-gcd   - Run ECC CLI flow for gcd example"
	@echo "  make demo-soc   - Run ECC CLI flow for SoCExamples filelist"
	@echo "  make demo-retrosoc - Run ECC CLI flow for retroSoC (mini, ICS55, minimal config)"
	@echo "  make docker-build  - Build clean Docker verification image"
	@echo "  make docker-verify - Alias of docker-verify-gcd"
	@echo "  make docker-verify-gcd - Run setup + gcd demo in clean Docker, verify workspace stages"
	@echo "  make docker-verify-soc - Run setup + soc demo in clean Docker, verify workspace stages"
	@echo "  make docker-verify-retrosoc - Run setup + retroSoC mini/ICS55 demo in clean Docker, verify workspace stages"
	@echo "  make docker-verify-all - Run setup + gcd + soc demos in clean Docker, verify workspace stages"
	@echo "                      (optional override: RUN_GCD=0/1 RUN_SOC=0/1 RUN_RETROSOC=0/1 make docker-verify)"
	@echo "                      (optional: UPDATE_SUBMODULES=1 make docker-verify)"
	@echo "                      (optional: USE_SSH_KEY=1 make docker-verify)"
	@echo "                      (optional: SSH_DIR=~/.ssh USE_SSH_KEY=1 make docker-verify)"
	@echo "                      (optional: USE_PROXY=true make docker-verify)"
	@echo "                      (optional: GH_PROXY=https://gh-proxy.org/ USE_PROXY=true make docker-verify)"
	@echo "                      (optional: TOOL=wget USE_PROXY=true make docker-verify)"

setup: init-submodules setup-pdk install-ecc

init-submodules:
	git submodule update --init --recursive

setup-pdk:
	$(MAKE) -C pdk/icsprout55-pdk unzip

install-ecc:
	nix build $(ECC_CLI)

demo-gcd:
	nix run $(ECC_CLI) -- --workspace $(GCD_WS) \
		--rtl ./eda/ecc/docs/examples/gcd/gcd.v \
		--design gcd \
		--top gcd \
		--clock clk \
		--pdk-root $(PDK_ROOT)

demo-soc:
	nix run $(ECC_CLI) -- --workspace $(SOC_WS) \
		--rtl ./ip/SoCExamples/soc/filelist.f \
		--design ysyxSoCASIC \
		--top ysyxSoCASIC \
		--clock clock \
		--pdk-root $(PDK_ROOT) \
		--freq 200

demo-retrosoc:
	mkdir -p $(dir $(RETROSOC_FLIST))
	( \
		while IFS= read -r line; do \
			[ -z "$$line" ] && continue; \
			case "$$line" in \
				\#*) ;; \
				/pdk/*) printf '%s\n' "$(abspath $(PDK_ROOT))$$line" ;; \
				*) printf '%s\n' "$$line" ;; \
			esac; \
		done < $(RETROSOC_PDK_FLIST); \
		printf '%s\n' \
			'+define+PDK_ICS55' \
			'+define+CORE_PICORV32' \
			'+define+IP_NONE' \
			'+define+SIMU_VCS' \
			'+define+SV_ASSRT_DISABLE' \
			'+define+SYNTHESIS'; \
		cat $(RETROSOC_MINI_FLIST_DIR)/sys_def.fl; \
		for fl in inc.fl ip.fl tech.fl core_picorv32.fl top.fl; do \
			while IFS= read -r line; do \
				[ -z "$$line" ] && continue; \
				case "$$line" in \
					\#*) ;; \
					+incdir+*) p="$${line#+incdir+}"; printf '+incdir+%s/%s\n' "$(RETROSOC_MINI_FLIST_DIR)" "$$p" ;; \
					*) printf '%s/%s\n' "$(RETROSOC_MINI_FLIST_DIR)" "$$line" ;; \
				esac; \
			done < $(RETROSOC_MINI_FLIST_DIR)/$$fl; \
		done; \
	) > $(RETROSOC_FLIST)
	nix run $(ECC_CLI) -- --workspace $(RETROSOC_WS) \
		--rtl $(RETROSOC_FLIST) \
		--design retrosoc_asic \
		--top retrosoc_asic \
		--clock extclk_i_pad \
		--pdk-root $(PDK_ROOT)

docker-build:
	docker build --no-cache -f Dockerfile.verify -t ecos-studio-verify:latest .

docker-verify:
	RUN_GCD=1 RUN_SOC=0 bash ./scripts/verify-demos-in-docker.sh

docker-verify-gcd:
	RUN_GCD=1 RUN_SOC=0 bash ./scripts/verify-demos-in-docker.sh

docker-verify-soc:
	RUN_GCD=0 RUN_SOC=1 bash ./scripts/verify-demos-in-docker.sh

docker-verify-retrosoc:
	RUN_GCD=0 RUN_SOC=0 RUN_RETROSOC=1 bash ./scripts/verify-demos-in-docker.sh

docker-verify-all:
	RUN_GCD=1 RUN_SOC=1 RUN_RETROSOC=1 bash ./scripts/verify-demos-in-docker.sh
