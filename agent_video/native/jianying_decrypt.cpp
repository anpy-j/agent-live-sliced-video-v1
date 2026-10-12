// Read a copy of an encrypted draft through the user's installed Jianying library.
// C++ owns libc++ string construction, return-value ABI, and destruction.
#include <dlfcn.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

int main(int argc, char** argv) {
    constexpr std::size_t max_bytes = 512ULL * 1024 * 1024;
    if (argc != 4) return 64;
    try {
        std::ifstream input(argv[2], std::ios::binary | std::ios::ate);
        if (!input) { std::cerr << "cannot read draft"; return 1; }
        const auto size = input.tellg();
        if (size <= 0 || static_cast<unsigned long long>(size) > max_bytes) {
            std::cerr << "invalid draft size"; return 1;
        }
        input.seekg(0);
        const std::string encrypted((std::istreambuf_iterator<char>(input)),
                                    std::istreambuf_iterator<char>());
        if (encrypted.size() != static_cast<std::size_t>(size)) {
            std::cerr << "draft changed while reading"; return 1;
        }
        void* library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
        if (!library) { std::cerr << dlerror(); return 2; }
        using Decrypt = std::string (*)(void*, const std::string&, const std::string&, bool&);
        auto decrypt = reinterpret_cast<Decrypt>(dlsym(library,
            "_ZN4lvve12EncryptUtils7decryptERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES9_Rb"));
        if (!decrypt) { std::cerr << "unsupported Jianying decrypt symbol: " << dlerror(); return 3; }
        bool ok = false;
        const std::string parameters = "{}";
        const std::string plain = decrypt(nullptr, encrypted, parameters, ok);
        if (!ok || plain.empty() || plain.size() > max_bytes) {
            std::cerr << "Jianying library rejected draft"; return 4;
        }
        std::ofstream output(argv[3], std::ios::binary | std::ios::trunc);
        output.write(plain.data(), static_cast<std::streamsize>(plain.size()));
        if (!output) { std::cerr << "cannot write temporary result"; return 5; }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what();
        return 6;
    }
}
