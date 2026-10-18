package oshi.hardware;

// HANDWRITTEN: see oshi/SystemInfo.java.

/** What the JVM knows about the machine it is running on; the rest is a browser's business, not ours. */
public final class CentralProcessor {

    private final ProcessorIdentifier identifier = new ProcessorIdentifier();
    private final int processors = Runtime.getRuntime().availableProcessors();

    public ProcessorIdentifier getProcessorIdentifier() {
        return identifier;
    }

    public int getLogicalProcessorCount() {
        return processors;
    }

    public int getPhysicalProcessorCount() {
        return processors;
    }

    public int getPhysicalPackageCount() {
        return 1;
    }

    /** Load ticks: this shim has no CPU counters to read, so every tick is idle. */
    public long[][] getProcessorCpuLoadTicks() {
        return new long[processors][8];
    }

    public double[] getProcessorCpuLoadBetweenTicks(long[][] previous) {
        return new double[processors];
    }

    public static final class ProcessorIdentifier {
        public String getVendor() {
            return "WebMC";
        }

        public String getName() {
            return "a browser JVM";
        }

        public String getIdentifier() {
            return "WebMC";
        }

        public String getMicroarchitecture() {
            return "";
        }

        public long getVendorFreq() {
            return 0L;
        }
    }
}
