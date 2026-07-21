package cli

import (
	"eDBG/config"
	"testing"
)

func TestPlainSymbolDropsTheColourWrapper(t *testing.T) {
	// GetSymbol formats for a terminal; a CSV field wants the name alone.
	got := plainSymbol(config.GREEN + "<Java_com_foo_bar>" + config.NC)
	if got != "Java_com_foo_bar" {
		t.Fatalf("got %q", got)
	}
}

func TestPlainSymbolOnAnUnresolvedAddressIsEmpty(t *testing.T) {
	if got := plainSymbol(""); got != "" {
		t.Fatalf("got %q", got)
	}
}

func TestPlainSymbolLeavesAnAlreadyPlainNameAlone(t *testing.T) {
	if got := plainSymbol("memcpy"); got != "memcpy" {
		t.Fatalf("got %q", got)
	}
}
