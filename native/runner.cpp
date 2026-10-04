// Structured JSONL bridge, pinned to whisper.cpp v1.8.3. No stdout log parsing.
#include "whisper.h"
#include "ggml-backend.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <vector>

static std::string quote(const std::string &s) {
    std::ostringstream o; o << '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"': o << "\\\""; break;
            case '\\': o << "\\\\"; break;
            case '\n': o << "\\n"; break;
            case '\r': o << "\\r"; break;
            case '\t': o << "\\t"; break;
            default: if (c < 32) o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
                     else o << c;
        }
    }
    return o.str() + '"';
}
static void emit(const std::string &s) { std::cout << s << std::endl; }
static std::string hex_bytes(const std::string &s) {
    static const char *digits = "0123456789abcdef";
    std::string out; out.reserve(s.size() * 2);
    for (unsigned char c : s) { out += digits[c >> 4]; out += digits[c & 15]; }
    return out;
}
static void logger(enum ggml_log_level, const char *text, void *) { std::cerr << text; }

static bool gpu(ggml_backend_dev_t d) {
    auto t = ggml_backend_dev_type(d);
    return t == GGML_BACKEND_DEVICE_TYPE_GPU || t == GGML_BACKEND_DEVICE_TYPE_IGPU;
}
static std::string device_json(ggml_backend_dev_t d, int index) {
    size_t free = 0, total = 0;
    ggml_backend_dev_memory(d, &free, &total);
    return "{\"name\":" + quote(ggml_backend_dev_description(d)) +
        ",\"id\":" + std::to_string(index) + ",\"gpu\":" + (gpu(d) ? "true" : "false") +
        ",\"memory_total\":" + std::to_string(total) + ",\"memory_free\":" + std::to_string(free) + "}";
}
static void probe() {
    ggml_backend_load_all();
    std::string out = "{\"protocol\":1,\"whisper_version\":\"1.8.3\",\"backend\":" + quote(LOCAL_BACKEND) + ",\"devices\":[";
    for (size_t i = 0; i < ggml_backend_dev_count(); ++i) {
        if (i) out += ',';
        out += device_json(ggml_backend_dev_get(i), int(i));
    }
    emit(out + "]}");
}

struct Callbacks { double offset = 0, duration = 0, total = 0, processed = 0; bool words = false; std::string last_text; };
static void emit_progress(Callbacks &c, double position) {
    // VAD padding can overlap intervals; already processed source time never regresses.
    c.processed = std::max(c.processed, std::min(c.total, position));
    emit("{\"event\":\"progress\",\"processed\":" + std::to_string(c.processed) +
         ",\"total\":" + std::to_string(c.total) + "}");
}
static void progress(whisper_context *, whisper_state *, int percent, void *userdata) {
    auto &c = *static_cast<Callbacks *>(userdata);
    emit_progress(c, c.offset + c.duration * std::clamp(percent, 0, 100) / 100.0);
}
static void segments(whisper_context *ctx, whisper_state *, int count, void *userdata) {
    auto &c = *static_cast<Callbacks *>(userdata);
    int end = whisper_full_n_segments(ctx);
    for (int i = end - count; i < end; ++i) {
        double start = c.offset + whisper_full_get_segment_t0(ctx, i) / 100.0;
        double stop = std::min({c.total, c.offset + c.duration, c.offset + whisper_full_get_segment_t1(ctx, i) / 100.0});
        std::string text = whisper_full_get_segment_text(ctx, i);
        c.last_text = text;
        std::string tokens = "[";
        double probability = 0, logprob = 0; int nprob = 0;
        for (int j = 0; j < whisper_full_n_tokens(ctx, i); ++j) {
            auto t = whisper_full_get_token_data(ctx, i, j);
            if (t.id >= whisper_token_eot(ctx)) continue;
            probability += t.p; logprob += std::log(std::max(double(t.p), 1e-10)); ++nprob;
            if (tokens.size() > 1) tokens += ',';
            std::string token_text = whisper_full_get_token_text(ctx, i, j);
            tokens += "{\"text\":" + quote(token_text) + ",\"bytes_hex\":" + quote(hex_bytes(token_text)) +
                ",\"id\":" + std::to_string(t.id) +
                ",\"start\":" + (c.words && t.t0 >= 0 ? std::to_string(c.offset + t.t0 / 100.0) : "null") +
                ",\"end\":" + (c.words && t.t1 >= 0 ? std::to_string(std::min(c.total, c.offset + t.t1 / 100.0)) : "null") +
                ",\"probability\":" + std::to_string(t.p) + "}";
        }
        emit("{\"event\":\"segment\",\"start\":" + std::to_string(start) + ",\"end\":" + std::to_string(stop) +
             ",\"text\":" + quote(text) + ",\"confidence\":" + (nprob ? std::to_string(probability / nprob) : "null") +
             ",\"avg_logprob\":" + (nprob ? std::to_string(logprob / nprob) : "null") +
             ",\"no_speech_probability\":" + std::to_string(whisper_full_get_segment_no_speech_prob(ctx, i)) +
             ",\"tokens\":" + tokens + "]}");
    }
}

static uint32_t u32(std::ifstream &f) { uint32_t x = 0; f.read(reinterpret_cast<char *>(&x), 4); return x; }
static uint16_t u16(std::ifstream &f) { uint16_t x = 0; f.read(reinterpret_cast<char *>(&x), 2); return x; }
struct Audio {
    std::ifstream file;
    uint64_t frames = 0;
    explicit Audio(const std::string &path): file(std::filesystem::u8path(path), std::ios::binary) {
        if (!file) throw std::runtime_error("Cannot open normalized audio");
        char id[4]; file.read(id, 4);
        if (std::memcmp(id, "RIFF", 4) && std::memcmp(id, "RF64", 4)) throw std::runtime_error("Expected WAV audio");
        u32(file); file.read(id, 4);
        if (std::memcmp(id, "WAVE", 4)) throw std::runtime_error("Invalid WAV header");
        bool format = false;
        while (file.read(id, 4)) {
            uint32_t size = u32(file); auto pos = file.tellg();
            if (!std::memcmp(id, "fmt ", 4)) {
                auto encoding = u16(file), channels = u16(file);
                auto rate = u32(file); u32(file); u16(file); auto bits = u16(file);
                if (encoding != 1 || channels != 1 || rate != 16000 || bits != 16)
                    throw std::runtime_error("Audio must be 16 kHz mono PCM16");
                format = true;
            } else if (!std::memcmp(id, "data", 4)) {
                if (!format) throw std::runtime_error("Missing WAV format");
                auto begin = file.tellg(); file.seekg(0, std::ios::end);
                auto available = static_cast<uint64_t>(file.tellg() - begin);
                frames = (size == 0xffffffffu ? available : std::min<uint64_t>(size, available)) / 2;
                file.seekg(begin); return;
            }
            file.seekg(pos + std::streamoff(size + (size & 1)));
        }
        throw std::runtime_error("No PCM data in WAV");
    }
};

int main(int argc, char **argv) {
    try {
        whisper_log_set(logger, nullptr);
        ggml_log_set(logger, nullptr);
        if (argc == 2 && std::string(argv[1]) == "--probe") { probe(); return 0; }
        std::map<std::string, std::string> args;
        for (int i = 1; i + 1 < argc; i += 2) args[argv[i]] = argv[i + 1];
        if (!args.count("--model") || !args.count("--audio")) throw std::runtime_error("Missing model or audio argument");
        bool use_gpu = std::string(LOCAL_BACKEND) != "cpu";
        ggml_backend_load_all();
        ggml_backend_dev_t selected = nullptr;
        int gpu_index = 0;
        for (size_t i = 0; i < ggml_backend_dev_count(); ++i) {
            auto d = ggml_backend_dev_get(i);
            if (use_gpu && gpu(d)) { selected = d; break; }
            if (!use_gpu && ggml_backend_dev_type(d) == GGML_BACKEND_DEVICE_TYPE_CPU) selected = d;
        }
        if (!selected) throw std::runtime_error("Requested backend has no compatible device. Try CPU.");
        if (use_gpu) {
            auto test = ggml_backend_dev_init(selected, nullptr);
            if (!test) throw std::runtime_error("GPU initialization failed. Try CPU.");
            ggml_backend_free(test);
        }
        emit("{\"event\":\"device\",\"backend\":" + quote(LOCAL_BACKEND) + ",\"device\":" + device_json(selected, gpu_index) + "}");
        auto cp = whisper_context_default_params(); cp.use_gpu = use_gpu; cp.gpu_device = gpu_index;
        // Standard token timestamps avoid the large extra allocation used by DTW.
        emit("{\"event\":\"stage\",\"stage\":\"Loading model\"}");
        // std::filesystem opens UTF-8 model paths correctly on Windows too.
        std::ifstream model_file(std::filesystem::u8path(args["--model"]), std::ios::binary);
        if (!model_file) throw std::runtime_error("Cannot open the model file");
        whisper_model_loader loader;
        loader.context = &model_file;
        loader.read = [](void *context, void *output, size_t count) -> size_t {
            auto &f = *static_cast<std::ifstream *>(context);
            f.read(static_cast<char *>(output), std::streamsize(count)); return size_t(f.gcount());
        };
        loader.eof = [](void *context) -> bool { return static_cast<std::ifstream *>(context)->eof(); };
        loader.close = [](void *context) { static_cast<std::ifstream *>(context)->close(); };
        std::unique_ptr<whisper_context, decltype(&whisper_free)> ctx(whisper_init_with_params(&loader, cp), whisper_free);
        if (!ctx) throw std::runtime_error("Whisper model could not be loaded. Check the model and available memory.");
        Audio audio(args["--audio"]);
        Callbacks callbacks; callbacks.total = audio.frames / 16000.0; callbacks.words = args["--words"] == "1";
        auto params = whisper_full_default_params(WHISPER_SAMPLING_BEAM_SEARCH);
        params.n_threads = std::stoi(args["--threads"]);
        params.beam_search.beam_size = std::stoi(args["--beam"]);
        params.temperature = std::stof(args["--temperature"]);
        params.translate = args["--translate"] == "1";
        params.suppress_nst = args["--suppress"] == "1";
        params.no_context = true; // Never carry a stuck decoder state between chunks.
        params.no_speech_thold = .6f;
        params.logprob_thold = -1.0f;
        std::string lang = args["--language"];
        if (lang != "auto" && whisper_lang_id(lang.c_str()) < 0) throw std::runtime_error("Unsupported language");
        if (!whisper_is_multilingual(ctx.get())) lang = "en";
        params.language = lang.c_str(); params.detect_language = false;
        params.print_realtime = false; params.print_progress = false; params.print_timestamps = false;
        params.token_timestamps = callbacks.words;
        params.new_segment_callback = segments; params.new_segment_callback_user_data = &callbacks;
        params.progress_callback = progress; params.progress_callback_user_data = &callbacks;
        std::unique_ptr<whisper_vad_context, decltype(&whisper_vad_free)> vad(nullptr, whisper_vad_free);
        if (!args["--vad-model"].empty()) {
            auto vp = whisper_vad_default_context_params(); vp.n_threads = params.n_threads; vp.use_gpu = false;
            // Use the loader interface to preserve Unicode file paths on Windows.
            std::ifstream vf(std::filesystem::u8path(args["--vad-model"]), std::ios::binary);
            whisper_model_loader vl; vl.context = &vf; vl.read = loader.read; vl.eof = loader.eof; vl.close = loader.close;
            vad.reset(vf ? whisper_vad_init_with_params(&vl, vp) : nullptr);
            if (!vad) emit("{\"event\":\"warning\",\"message\":\"VAD initialization failed; continued without speech detection.\"}");
        }
        uint64_t done = 0;
        std::vector<int16_t> pcm(300 * 16000);
        std::vector<float> samples; samples.reserve(pcm.size());
        emit("{\"event\":\"stage\",\"stage\":\"Transcribing\"}");
        while (done < audio.frames) {
            auto count = std::min<uint64_t>(pcm.size(), audio.frames - done);
            audio.file.read(reinterpret_cast<char *>(pcm.data()), count * 2);
            if (uint64_t(audio.file.gcount()) != count * 2) throw std::runtime_error("Truncated normalized audio");
            // Prefer a quiet boundary in the final eight seconds of each bounded chunk.
            if (done + count < audio.frames) {
                size_t cut = count; double best = 1e12;
                for (size_t pos = count - 8 * 16000; pos + 8000 <= count; pos += 8000) {
                    double energy = 0;
                    for (size_t k = pos; k < pos + 8000; ++k) energy += double(pcm[k]) * pcm[k];
                    if (energy < best) { best = energy; cut = pos + 4000; }
                }
                audio.file.seekg(-std::streamoff((count - cut) * 2), std::ios::cur); count = cut;
            }
            samples.resize(count);
            for (size_t i = 0; i < count; ++i) samples[i] = pcm[i] / 32768.0f;
            callbacks.offset = done / 16000.0; callbacks.duration = count / 16000.0;
            std::vector<std::pair<size_t, size_t>> spans;
            if (vad) {
                auto vp = whisper_vad_default_params(); vp.max_speech_duration_s = 60.0f; vp.speech_pad_ms = 200;
                std::unique_ptr<whisper_vad_segments, decltype(&whisper_vad_free_segments)> found(
                    whisper_vad_segments_from_samples(vad.get(), vp, samples.data(), int(count)), whisper_vad_free_segments);
                if (!found) throw std::runtime_error("VAD inference failed. Disable VAD and retry.");
                for (int i = 0; i < whisper_vad_segments_n_segments(found.get()); ++i) {
                    // The public VAD segment API uses centiseconds.
                    size_t a = std::min<size_t>(count, size_t(std::max(0.0f, whisper_vad_segments_get_segment_t0(found.get(), i)) * 160));
                    size_t b = std::min<size_t>(count, size_t(std::max(0.0f, whisper_vad_segments_get_segment_t1(found.get(), i)) * 160));
                    if (b > a) spans.push_back({a, b});
                }
                emit("{\"event\":\"vad\",\"speech_spans\":" + std::to_string(spans.size()) + "}");
            } else spans.push_back({0, size_t(count)});
            for (auto span : spans) {
                callbacks.offset = (done + span.first) / 16000.0;
                callbacks.duration = (span.second - span.first) / 16000.0;
                params.initial_prompt = nullptr;
                if (whisper_full(ctx.get(), params, samples.data() + span.first, int(span.second - span.first))) throw std::runtime_error("Whisper inference failed. Check the backend log.");
                if (lang == "auto") {
                    lang = whisper_lang_str(whisper_full_lang_id(ctx.get())); params.language = lang.c_str();
                    emit("{\"event\":\"language\",\"language\":" + quote(lang) + "}");
                }
            }
            done += count;
            emit_progress(callbacks, done / 16000.0);
        }
        emit("{\"event\":\"done\",\"language\":" + quote(lang == "auto" ? "und" : lang) + ",\"duration\":" + std::to_string(callbacks.total) + "}");
        return 0;
    } catch (const std::exception &error) {
        emit("{\"event\":\"error\",\"message\":" + quote(error.what()) + "}");
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
