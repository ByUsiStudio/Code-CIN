package compiler

import (
	"fmt"
	"strconv"
	"strings"
)

// ---------------- 词法分析 ----------------

// Token 词法单元。
type Token struct {
	kind     string
	ival     int64
	fval     float64
	isFloat  bool
	sval     string
	line     int
	filename string
}

var keywords = map[string]bool{
	"struct": true, "function": true, "return": true, "if": true, "else": true,
	"while": true, "for": true, "do": true, "switch": true, "case": true,
	"default": true, "break": true, "continue": true, "true": true, "false": true,
	"int": true, "float": true, "bool": true, "string": true, "void": true,
	"char": true, "short": true, "long": true, "unsigned": true,
	"set": true, "add": true, "subtract": true, "multiply": true, "divide": true,
	"increment": true, "decrement": true,
}

var baseTypeWords = map[string]bool{
	"int": true, "float": true, "bool": true, "string": true,
	"char": true, "short": true, "long": true, "unsigned": true,
}

var compoundAssign = map[string]string{
	"PLUSEQ": "+=", "MINUSEQ": "-=", "STAREQ": "*=", "SLASHEQ": "/=",
	"PERCENTEQ": "%=", "ANDEQ": "&=", "OREQ": "|=", "XOREQ": "^=",
	"SHLEQ": "<<=", "SHREQ": ">>=",
}

var singleOps = map[byte]string{
	'{': "LBRACE", '}': "RBRACE", '(': "LPAREN", ')': "RPAREN",
	'[': "LBRACKET", ']': "RBRACKET", ',': "COMMA", ';': "SEMI",
	'+': "PLUS", '-': "MINUS", '*': "STAR", '/': "SLASH", '%': "PERCENT",
	'=': "ASSIGN", '<': "LT", '>': "GT", '!': "BANG", '.': "DOT",
	'?': "QUESTION", ':': "COLON", '&': "AMP", '|': "PIPE", '^': "CARET",
	'~': "TILDE",
}

var strEscapes = map[byte]byte{
	'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', '0': 0,
}

// tokenize 把 CIN 源码切分为 token 流 (含续行处理与 BOM 容忍)。
func tokenize(source, filename string) ([]Token, error) {
	source = strings.TrimPrefix(source, "\ufeff") // 容忍 UTF-8 BOM
	var tokens []Token
	i, line, n := 0, 1, len(source)

	skipBlock := func() {
		i += 2
		for i < n-1 && !(source[i] == '*' && source[i+1] == '/') {
			if source[i] == '\n' {
				line++
			}
			i++
		}
		i += 2
	}

	for i < n {
		c := source[i]
		switch {
		case c == '\n':
			tokens = append(tokens, Token{kind: "NL", line: line})
			line++
			i++
		case c == ' ' || c == '\t' || c == '\r':
			i++
		case c == '/' && i+1 < n && source[i+1] == '/':
			for i < n && source[i] != '\n' {
				i++
			}
		case c == '/' && i+1 < n && source[i+1] == '*':
			skipBlock()
		case c == '"':
			startLine := line
			i++
			var buf strings.Builder
			for i < n && source[i] != '"' {
				if source[i] == '\\' && i+1 < n {
					esc := source[i+1]
					if b, ok := strEscapes[esc]; ok {
						buf.WriteByte(b)
					} else {
						buf.WriteByte(esc)
					}
					i += 2
				} else {
					if source[i] == '\n' {
						line++
					}
					buf.WriteByte(source[i])
					i++
				}
			}
			i++ // closing quote
			tokens = append(tokens, Token{kind: "STRING", sval: buf.String(), line: startLine})
		case c >= '0' && c <= '9' || (c == '.' && i+1 < n && source[i+1] >= '0' && source[i+1] <= '9'):
			start := i
			isFloat := false
			if c == '0' && i+1 < n && strings.IndexByte("xXbBoO", source[i+1]) >= 0 {
				baseChar := source[i+1]
				var base int
				var allowed string
				switch baseChar {
				case 'x', 'X':
					base, allowed = 16, "0123456789abcdefABCDEF_"
				case 'b', 'B':
					base, allowed = 2, "01_"
				default:
					base, allowed = 8, "01234567_"
				}
				i += 2
				dstart := i
				for i < n && strings.IndexByte(allowed, source[i]) >= 0 {
					i++
				}
				digits := source[dstart:i]
				hasDigit := false
				for _, ch := range digits {
					if ch != '_' {
						hasDigit = true
						break
					}
				}
				if i == dstart || !hasDigit {
					return nil, fmt.Errorf("Malformed numeric literal at line %d", line)
				}
				for i < n && strings.IndexByte("uUlL", source[i]) >= 0 {
					i++
				}
				if i < n && (source[i] == 'f' || source[i] == 'F') {
					isFloat = true
					i++
				}
				clean := strings.ReplaceAll(digits, "_", "")
				v, err := strconv.ParseUint(clean, base, 64)
				if err != nil {
					return nil, fmt.Errorf("Malformed numeric literal at line %d", line)
				}
				_ = start
				if isFloat {
					tokens = append(tokens, Token{kind: "FLOAT", fval: float64(v), isFloat: true, line: line})
				} else {
					tokens = append(tokens, Token{kind: "NUMBER", ival: int64(v), line: line})
				}
			} else {
				for i < n && (source[i] >= '0' && source[i] <= '9' || source[i] == '.' || source[i] == '_') {
					if source[i] == '.' {
						isFloat = true
					}
					i++
				}
				if i < n && (source[i] == 'e' || source[i] == 'E') {
					isFloat = true
					i++
					if i < n && (source[i] == '+' || source[i] == '-') {
						i++
					}
					for i < n && (source[i] >= '0' && source[i] <= '9' || source[i] == '_') {
						i++
					}
				}
				for i < n && strings.IndexByte("uUlL", source[i]) >= 0 {
					i++
				}
				if i < n && (source[i] == 'f' || source[i] == 'F') {
					isFloat = true
					i++
				}
				text := strings.ReplaceAll(source[start:i], "_", "")
				if isFloat {
					f, err := strconv.ParseFloat(text, 64)
					if err != nil {
						return nil, fmt.Errorf("Malformed numeric literal at line %d", line)
					}
					tokens = append(tokens, Token{kind: "FLOAT", fval: f, isFloat: true, line: line})
				} else {
					iv, err := strconv.ParseInt(text, 10, 64)
					if err != nil {
						return nil, fmt.Errorf("Malformed numeric literal at line %d", line)
					}
					tokens = append(tokens, Token{kind: "NUMBER", ival: iv, line: line})
				}
			}
		case c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c == '_':
			start := i
			for i < n && (isAlnum(source[i]) || source[i] == '_') {
				i++
			}
			tokens = append(tokens, Token{kind: "IDENT", sval: source[start:i], line: line})
		case c == '-' && i+1 < n && source[i+1] == '>':
			tokens = append(tokens, Token{kind: "ARROW", sval: "->", line: line})
			i += 2
		case c == '\'':
			startLine := line
			i++
			if i >= n {
				return nil, fmt.Errorf("Unterminated char literal at line %d", startLine)
			}
			var val int64
			if source[i] == '\\' && i+1 < n {
				esc := source[i+1]
				escMap := map[byte]int64{'n': 10, 't': 9, 'r': 13, '0': 0, 'a': 7, 'b': 8,
					'f': 12, 'v': 11, '\'': 39, '"': 34, '\\': 92}
				if v, ok := escMap[esc]; ok {
					val = v
				} else {
					val = int64(esc)
				}
				i += 2
			} else {
				val = int64(source[i])
				i++
			}
			if i >= n || source[i] != '\'' {
				return nil, fmt.Errorf("Unterminated char literal at line %d", startLine)
			}
			i++
			tokens = append(tokens, Token{kind: "NUMBER", ival: val, line: line})
		default:
			if strings.IndexByte("+-*/%", c) >= 0 && i+1 < n && source[i+1] == '=' {
				kindMap := map[byte]string{'+': "PLUSEQ", '-': "MINUSEQ", '*': "STAREQ",
					'/': "SLASHEQ", '%': "PERCENTEQ"}
				tokens = append(tokens, Token{kind: kindMap[c], sval: string(c) + "=", line: line})
				i += 2
			} else if strings.IndexByte("&|^", c) >= 0 && i+1 < n && source[i+1] == '=' {
				kindMap := map[byte]string{'&': "ANDEQ", '|': "OREQ", '^': "XOREQ"}
				tokens = append(tokens, Token{kind: kindMap[c], sval: string(c) + "=", line: line})
				i += 2
			} else if (c == '<' || c == '>') && i+2 < n && source[i+1] == c && source[i+2] == '=' {
				k := "SHLEQ"
				if c == '>' {
					k = "SHREQ"
				}
				tokens = append(tokens, Token{kind: k, sval: string(c) + string(c) + "=", line: line})
				i += 3
			} else if (c == '<' || c == '>') && i+1 < n && source[i+1] == c {
				k := "SHL"
				if c == '>' {
					k = "SHR"
				}
				tokens = append(tokens, Token{kind: k, sval: string(c) + string(c), line: line})
				i += 2
			} else if c == '+' && i+1 < n && source[i+1] == '+' {
				tokens = append(tokens, Token{kind: "INC", sval: "++", line: line})
				i += 2
			} else if c == '-' && i+1 < n && source[i+1] == '-' {
				tokens = append(tokens, Token{kind: "DEC", sval: "--", line: line})
				i += 2
			} else if c == '=' && i+1 < n && source[i+1] == '=' {
				tokens = append(tokens, Token{kind: "EQ", sval: "==", line: line})
				i += 2
			} else if c == '!' && i+1 < n && source[i+1] == '=' {
				tokens = append(tokens, Token{kind: "NEQ", sval: "!=", line: line})
				i += 2
			} else if c == '<' && i+1 < n && source[i+1] == '=' {
				tokens = append(tokens, Token{kind: "LE", sval: "<=", line: line})
				i += 2
			} else if c == '>' && i+1 < n && source[i+1] == '=' {
				tokens = append(tokens, Token{kind: "GE", sval: ">=", line: line})
				i += 2
			} else if c == '&' && i+1 < n && source[i+1] == '&' {
				tokens = append(tokens, Token{kind: "AND", sval: "&&", line: line})
				i += 2
			} else if c == '|' && i+1 < n && source[i+1] == '|' {
				tokens = append(tokens, Token{kind: "OR", sval: "||", line: line})
				i += 2
			} else if k, ok := singleOps[c]; ok {
				tokens = append(tokens, Token{kind: k, sval: string(c), line: line})
				i++
			} else {
				return nil, fmt.Errorf("Unexpected character %q at line %d", c, line)
			}
		}
	}

	// 续行处理 (圆/方括号内或行尾运算符后的 NL 丢弃)
	var filtered []Token
	depth := 0
	openK := map[string]bool{"LPAREN": true, "LBRACKET": true}
	closeK := map[string]bool{"RPAREN": true, "RBRACKET": true}
	continueOps := map[string]bool{
		"PLUS": true, "MINUS": true, "STAR": true, "SLASH": true, "PERCENT": true,
		"ASSIGN": true, "LT": true, "GT": true, "LE": true, "GE": true, "EQ": true,
		"NEQ": true, "AND": true, "OR": true, "COMMA": true, "ARROW": true, "DOT": true,
		"PLUSEQ": true, "MINUSEQ": true, "STAREQ": true, "SLASHEQ": true,
		"PERCENTEQ": true, "INC": true, "DEC": true, "AMP": true, "PIPE": true,
		"CARET": true, "TILDE": true, "SHL": true, "SHR": true, "ANDEQ": true,
		"OREQ": true, "XOREQ": true, "SHLEQ": true, "SHREQ": true,
	}
	for _, tok := range tokens {
		if openK[tok.kind] {
			depth++
		} else if closeK[tok.kind] {
			if depth > 0 {
				depth--
			}
		}
		if tok.kind == "NL" {
			if depth > 0 {
				continue
			}
			if len(filtered) > 0 {
				prev := filtered[len(filtered)-1]
				if continueOps[prev.kind] {
					continue
				}
				if prev.kind == "NL" {
					continue
				}
			}
		}
		filtered = append(filtered, tok)
	}
	filtered = append(filtered, Token{kind: "EOF", line: line})
	return filtered, nil
}

func isAlnum(c byte) bool {
	return c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z'
}
