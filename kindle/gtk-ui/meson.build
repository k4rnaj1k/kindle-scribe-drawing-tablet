project('tablet-ui', 'cpp',
  version: '0.1.0',
  default_options: ['cpp_std=c++17'])

gtk2 = dependency('gtk+-2.0')

executable('tablet-ui',
  'src/main.cpp',
  dependencies: [gtk2],
  cpp_args: '-static-libstdc++',
  link_args: '-static-libstdc++',
  install: true)
