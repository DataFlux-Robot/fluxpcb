
/* host_test/regression.c - executed on the host compiler by the
 * firmware_regression gate. The logic under test is the very same
 * fw_logic.c built into the target firmware. */
#define HOST_TEST 1
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include "../main/fw_logic.h"
#include "../main/pin_config.h"

static int checks = 0;
#define CHECK(cond) do { checks++; if (!(cond)) { \
    printf("REGRESSION_FAIL at %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    return 1; } } while (0)

int main(void)
{
    /* pin map sanity: distinct, valid GPIO numbers */
    CHECK(PIN_LED_USER >= 0 && PIN_LED_USER <= 54);
    CHECK(PIN_BTN_BOOT >= 0 && PIN_BTN_BOOT <= 54);
    CHECK(PIN_SPI_MOSI != PIN_SPI_MISO);
    CHECK(PIN_SPI_SCK != PIN_SPI_MOSI);
    CHECK(PIN_SPI_SCK != PIN_SPI_MISO);

    /* boot sequence reaches RUN and stays there */
    fw_boot_t s = FW_BOOT_START;
    for (int i = 0; i < 5; i++) s = fw_boot_next(s);
    CHECK(s == FW_BOOT_RUN);
    CHECK(fw_boot_next(s) == FW_BOOT_RUN);

    /* debounce: stable accepted, bouncing rejected, empty rejected */
    uint8_t stable[5] = {1, 1, 1, 1, 1};
    uint8_t bounce[5] = {1, 0, 1, 1, 1};
    CHECK(fw_debounce_stable(stable, 5) == 1);
    CHECK(fw_debounce_stable(bounce, 5) == 0);
    CHECK(fw_debounce_stable(stable, 0) == 0);

    /* SPI soft loopback: deterministic pattern round-trip */
    uint8_t tx[16], rx[16];
    for (int i = 0; i < 16; i++) tx[i] = (uint8_t)(i * 7 + 3);
    memset(rx, 0, sizeof rx);
    fw_spi_loopback(tx, rx, 16);
    CHECK(memcmp(tx, rx, 16) == 0);

    /* LED timing math */
    CHECK(fw_led_blink_ms(1000, 50) == 500);
    CHECK(fw_led_blink_ms(1000, 0) == 0);
    CHECK(fw_led_blink_ms(1000, 200) == 1000);  /* clamped */

    printf("REGRESSION_PASS checks=%d\n", checks);
    return 0;
}
