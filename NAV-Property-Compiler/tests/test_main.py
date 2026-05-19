from nav_property_compiler.main import main


def test_main(capsys):
    main()
    captured = capsys.readouterr()
    assert "NAV-Property-Compiler" in captured.out
