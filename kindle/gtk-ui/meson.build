project('tablet-ui', 'cpp',
  version: '0.1.0',
  default_options: ['cpp_std=c++17'])

gtk2 = dependency('gtk+-2.0')
cc   = meson.get_compiler('cpp')
# libXrandr.so is already in the Kindle sysroot (part of the X11 stack).
# Use find_library so we don't require a xrandr.pc file in the sysroot.
xrandr = cc.find_library('Xrandr', required: true)

executable('tablet-ui',
  'src/main.cpp',
  dependencies: [gtk2, xrandr],
  cpp_args: '-static-libstdc++',
  link_args: '-static-libstdc++',
  install: true)
