import os

S = os.environ["OR_WORK"]


def write(c, blocks, labels=None, mode="w", xmin=None):
    with open(f"{S}/tr/{c}.csv", mode) as f:
        for page, meas, txt, *lab in blocks:
            labs = lab[0] if lab else labels
            for l, r in zip(labs, txt.split("|"), strict=True):
                y, n = r.split()
                f.write(
                    f"{page},{meas},{l},{y},{n}"
                    + (f",{xmin[(page, meas)]}" if xmin and (page, meas) in xmin else "")
                    + "\n"
                )
