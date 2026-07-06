from openeo_argoworkflows_executor.cli import _collect_result_files


def test_collect_result_files_recurses_and_skips_directories_and_hidden_files(tmp_path):
    nested = tmp_path / "package-output" / "items"
    nested.mkdir(parents=True)
    data = nested / "result.tif"
    data.write_bytes(b"tif")
    hidden = nested / ".hidden"
    hidden.write_text("skip")

    files = _collect_result_files(str(tmp_path))

    assert files == [str(data)]
