import marimo

__generated_with = "0.9.0"
app = marimo.App()


@app.cell
def __():
    import marimo as mo
    return (mo,)


@app.cell
def __(mo):
    mo.md("# Optio widget demo\n\nHello from marimo — this notebook is served through the optio widget proxy.")
    return


if __name__ == "__main__":
    app.run()
