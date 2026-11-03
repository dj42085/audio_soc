#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <generated/csr.h>
#include "libhelix-mp3/pub/mp3dec.h" 

// Forward declarations
void *malloc(size_t size);
void *calloc(size_t nmemb, size_t size);
void free(void *ptr);

// 1. Embedded Binary Pointers
extern unsigned char _binary_track_mp3_start[];
extern unsigned char _binary_track_mp3_end[];

// 2. Move heavy stack variables to global BSS space
static short pcm_buffer[1152 * 2]; 

// 3. Robust Memory Pool for Bare-Metal Helix
#define MP3_POOL_SIZE (48 * 1024)
static unsigned char mp3_memory_pool[MP3_POOL_SIZE];
static size_t mp3_pool_idx = 0;

// Track the last allocation to safely support Helix's initialization free() sequence
static void *last_alloc_ptr = NULL;
static size_t last_alloc_size = 0;

void *malloc(size_t size) {
    // Force 4-byte alignment for RISC-V hardware execution safety
    size = (size + 3) & ~3;
    
    if (mp3_pool_idx + size > MP3_POOL_SIZE) {
        puts("Error: MP3 Memory Pool Out of Memory!");
        return NULL; 
    }
    
    void *ptr = &mp3_memory_pool[mp3_pool_idx];
    last_alloc_ptr = ptr;
    last_alloc_size = size;
    
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
    if (ptr == NULL) return;
    
    // Simple LIFO free check: If Helix frees the absolute last thing it allocated 
    // (which it does during MP3InitDecoder initialization), we roll back the pointer.
    if (ptr == last_alloc_ptr) {
        mp3_pool_idx -= last_alloc_size;
        last_alloc_ptr = NULL;
        last_alloc_size = 0;
    }
}

int main(void) {
    HMP3Decoder hMP3Decoder;
    MP3FrameInfo mp3FrameInfo;
    
    unsigned char *read_ptr = _binary_track_mp3_start;
    int bytes_left = _binary_track_mp3_end - _binary_track_mp3_start;
    
    puts("Initializing Helix MP3 Decoding Pipeline...");
    
    hMP3Decoder = MP3InitDecoder();
    if (!hMP3Decoder) {
        puts("Critical Error: Could not allocate Helix Decoder context!");
        while(1);
    }

    puts("Decoder successfully initialized. Starting playback loop...");

    // Main Stream Decoding Loop
    while (bytes_left > 0) {
        int sync_offset = MP3FindSyncWord(read_ptr, bytes_left);
        if (sync_offset < 0) {
            puts("End of stream or synchronization lost.");
            break;
        }
        
        read_ptr += sync_offset;
        bytes_left -= sync_offset;

        int err = MP3Decode(hMP3Decoder, &read_ptr, &bytes_left, pcm_buffer, 0);
        
        if (err != ERR_MP3_NONE) {
            if (err == ERR_MP3_INDATA_UNDERFLOW) {
                puts("Stream underflow occurred.");
                break;
            }
            continue; 
        }

        MP3GetLastFrameInfo(hMP3Decoder, &mp3FrameInfo);
        int total_pcm_samples = mp3FrameInfo.outputSamps;
        
        printf("Decoded frame: %d samples, %d Hz\n", total_pcm_samples, mp3FrameInfo.samprate);

        // DAC code remains safely here...
    }

    puts("Playback completed cleanly.");
    MP3FreeDecoder(hMP3Decoder);
    while (1);
}

