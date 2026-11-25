// Release builds use the GUI subsystem on Windows: no console window ever appears.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    muse_lib::run();
}
