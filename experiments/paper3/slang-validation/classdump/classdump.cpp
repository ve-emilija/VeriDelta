// Syntax-level class extractor: prints "file<TAB>class<TAB>base" for every class declaration.
// No elaboration, no filelists, no UVM library needed.
#include "slang/syntax/SyntaxTree.h"
#include "slang/syntax/SyntaxVisitor.h"
#include "slang/syntax/AllSyntax.h"
#include <iostream>
#include <string>
using namespace slang; using namespace slang::syntax;

struct V : public SyntaxVisitor<V> {
    std::string file;
    void handle(const ClassDeclarationSyntax& c) {
        std::string base;
        if (c.extendsClause) {
            std::string s = c.extendsClause->baseName->toString();
            // strip whitespace and any #(...) parameters, keep scope-less last identifier
            size_t h = s.find('#'); if (h != std::string::npos) s = s.substr(0, h);
            size_t p = s.rfind("::"); if (p != std::string::npos) s = s.substr(p + 2);
            while (!s.empty() && isspace((unsigned char)s.back())) s.pop_back();
            size_t b = 0; while (b < s.size() && isspace((unsigned char)s[b])) b++;
            base = s.substr(b);
        }
        std::cout << file << '\t' << std::string(c.name.valueText()) << '\t' << base << '\n';
        visitDefault(c);
    }
};

int main(int argc, char** argv) {
    int errs = 0;
    for (int i = 1; i < argc; i++) {
        auto r = SyntaxTree::fromFile(argv[i]);
        if (!r) { std::cerr << "ERR\t" << argv[i] << '\n'; errs++; continue; }
        V v; v.file = argv[i];
        (*r)->root().visit(v);
    }
    return errs ? 1 : 0;
}
