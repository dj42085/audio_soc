#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <generated/csr.h>
#include "libhelix-mp3/pub/mp3dec.h" // Path to Helix API headers

void *malloc(size_t size);
void *calloc(size_t nmemb, size_t size);

// 1. Point to your raw MP3 data location in memory
extern unsigned char _binary_track_mp3_start[];
extern unsigned char _binary_track_mp3_end[];

// 2. Setup standard Helix runtime working blocks
#define MAINBUF_SIZE 1940   // Minimum required size for a full MP3 frame window
#define MAX_PCM_SAMPLES 2304 // 1152 samples per channel * 2 (stereo max)
#include <stddef.h>
#include <string.h>

// Allocate a 40KB pool for the MP3 decoder. 
// Helix typically requires ~25-30KB depending on mono/stereo and layer details.
#define MP3_POOL_SIZE (40 * 1024)
static unsigned char mp3_memory_pool[MP3_POOL_SIZE];
static size_t mp3_pool_idx = 0;

void *malloc(size_t size) {
    // Align allocations to a 4-byte boundary for RISC-V safety
    size = (size + 3) & ~3;
    
    if (mp3_pool_idx + size > MP3_POOL_SIZE) {
        // Out of memory error: handle appropriately for your SoC (e.g., printf)
        return NULL; 
    }
    
    void *ptr = &mp3_memory_pool[mp3_pool_idx];
    mp3_pool_idx += size;
    return ptr;
}

void *calloc(size_t nmemb, size_t size) {
    size_t total_size = nmemb * size;
    void *ptr = malloc(total_size);
    if (ptr) {
        memset(ptr, 0, total_size);
    }
    return ptr;
}

void free(void *ptr) {
    // Helix decoder structures are persistent until the app closes,
    // so we can safely leave this blank on a bare-metal target.
}

int main(void) {
    HMP3Decoder hMP3Decoder;
    MP3FrameInfo mp3FrameInfo;
    
    unsigned char *read_ptr = _binary_track_mp3_start;
    int bytes_left = _binary_track_mp3_end - _binary_track_mp3_start;
    
    puts("Initializing Helix MP3 Decoding Pipeline...");
    
    // Initialize the fixed-point decoder engine structure
    hMP3Decoder = MP3InitDecoder();
    if (!hMP3Decoder) {
        puts("Error: Could not allocate Helix Decoder context!");
        while(1);
    }

    short pcm_buffer[1152 * 2]; // 2304 words for max stereo frame size

    // Main Stream Decoding Loop
    while (bytes_left > 0) {
        // Find the next valid MP3 sync word (0xFFE0 / fr/ame boundary check)
        int sync_offset = MP3FindSyncWord(read_ptr, bytes_left);
        if (sync_offset < 0) {
            puts("End of stream or synchronization lost.");
            break;
        }
        
        read_ptr += sync_offset;
        bytes_left -= sync_offset;

        // Perform the frame decode action
        // Note: read_ptr and bytes_left are updated automatically inside the macro call
        int err = MP3Decode(hMP3Decoder, &read_ptr, &bytes_left, pcm_buffer, 0);
        
        if (err != ERR_MP3_NONE) {
            if (err == ERR_MP3_INDATA_UNDERFLOW) {
                puts("Stream underflow occurred.");
                break;
            }
            // Skip broken frame windows smoothly
            continue; 
        }

        // Extract metadata parsing configurations for the frame
        MP3GetLastFrameInfo(hMP3Decoder, &mp3FrameInfo);
        
        // At this stage, pcm_buffer contains valid 16-bit signed samples!
        // Total samples available = mp3FrameInfo.outputSamps
        int total_pcm_samples = mp3FrameInfo.outputSamps;
        
        // -------------------------------------------------------------
        // Custom DAC Ingestion Handshake
        // ------------------------------------------------------------

        puts("trying to write DAC sample");

        /*
        for (int i = 0; i < total_pcm_samples; i++) {
            short sample = pcm_buffer[i];
            
            // Loop while your custom DAC peripheral memory register claims the FIFO is full
            while (custom_dac_fifo_full_read() == 1) {
                // Spin wait or handle scheduling ticks
            }
            
            // Push raw 16-bit payload straight down your LiteX Control&Status Register (CSR) lane
            custom_dac_fifo_write_write(sample);
        }
        */
    }

    puts("Playback completed cleanly.");
    MP3FreeDecoder(hMP3Decoder);
    while (1);
}

