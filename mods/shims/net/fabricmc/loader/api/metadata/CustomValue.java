package net.fabricmc.loader.api.metadata;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A custom value from a mod's metadata: a string, an object, an array, or nothing. */
public interface CustomValue {
    boolean getAsBoolean();

    Number getAsNumber();

    String getAsString();

    CvArray getAsArray();

    CvObject getAsObject();

    CvType getType();

    enum CvType {
        STRING, NUMBER, BOOLEAN, ARRAY, OBJECT
    }

    /** The array form. */
    interface CvArray extends CustomValue, Iterable<CustomValue> {
        @Override
        Iterator<CustomValue> iterator();

        List<CustomValue> asList();

        int size();

        CustomValue get(int index);
    }

    /** The object form. */
    interface CvObject extends CustomValue, Iterable<Map.Entry<String, CustomValue>> {
        @Override
        Iterator<Map.Entry<String, CustomValue>> iterator();

        Map<String, CustomValue> asMap();

        boolean containsKey(String key);

        CustomValue get(String key);

        int size();
    }

    /** Reading a value out of parsed metadata. */
    final class Impl {
        private Impl() {
        }

        public static CustomValue of(Object value) {
            if (value instanceof Map<?, ?> map) {
                Map<String, CustomValue> values = new LinkedHashMap<>();
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    values.put(String.valueOf(entry.getKey()), of(entry.getValue()));
                }
                return new ObjectValue(values);
            }
            if (value instanceof List<?> list) {
                List<CustomValue> values = new ArrayList<>();
                for (Object item : list) {
                    values.add(of(item));
                }
                return new ArrayValue(values);
            }
            if (value instanceof Boolean flag) {
                return new ScalarValue(CvType.BOOLEAN, flag.toString(), flag, null);
            }
            if (value instanceof Number number) {
                return new ScalarValue(CvType.NUMBER, number.toString(), null, number);
            }
            if (value == null) {
                return new ScalarValue(CvType.STRING, "", null, null);
            }
            return new ScalarValue(CvType.STRING, String.valueOf(value), null, null);
        }

        private static UnsupportedOperationException wrong(CvType type, String wanted) {
            return new UnsupportedOperationException("this custom value is a " + type + ", not a " + wanted);
        }

        private record ScalarValue(CvType getType, String string, Boolean bool, Number number) implements CustomValue {
            @Override
            public boolean getAsBoolean() {
                if (this.bool != null) {
                    return this.bool;
                }
                if (this.number != null) {
                    return this.number.doubleValue() != 0;
                }
                return Boolean.parseBoolean(this.string);
            }

            @Override
            public Number getAsNumber() {
                if (this.number != null) {
                    return this.number;
                }
                try {
                    return Double.valueOf(this.string);
                } catch (NumberFormatException error) {
                    throw wrong(this.getType(), "number");
                }
            }

            @Override
            public String getAsString() {
                return this.string;
            }

            @Override
            public CvArray getAsArray() {
                throw wrong(this.getType(), "array");
            }

            @Override
            public CvObject getAsObject() {
                throw wrong(this.getType(), "object");
            }
        }

        private record ArrayValue(List<CustomValue> values) implements CvArray {
            @Override
            public Iterator<CustomValue> iterator() {
                return this.values.iterator();
            }

            @Override
            public List<CustomValue> asList() {
                return this.values;
            }

            @Override
            public int size() {
                return this.values.size();
            }

            @Override
            public CustomValue get(int index) {
                return this.values.get(index);
            }

            @Override
            public boolean getAsBoolean() {
                throw wrong(CvType.ARRAY, "boolean");
            }

            @Override
            public Number getAsNumber() {
                throw wrong(CvType.ARRAY, "number");
            }

            @Override
            public String getAsString() {
                throw wrong(CvType.ARRAY, "string");
            }

            @Override
            public CvArray getAsArray() {
                return this;
            }

            @Override
            public CvObject getAsObject() {
                throw wrong(CvType.ARRAY, "object");
            }

            @Override
            public CvType getType() {
                return CvType.ARRAY;
            }
        }

        private record ObjectValue(Map<String, CustomValue> values) implements CvObject {
            @Override
            public Iterator<Map.Entry<String, CustomValue>> iterator() {
                return this.values.entrySet().iterator();
            }

            @Override
            public Map<String, CustomValue> asMap() {
                return this.values;
            }

            @Override
            public boolean containsKey(String key) {
                return this.values.containsKey(key);
            }

            @Override
            public CustomValue get(String key) {
                return this.values.get(key);
            }

            @Override
            public int size() {
                return this.values.size();
            }

            @Override
            public boolean getAsBoolean() {
                throw wrong(CvType.OBJECT, "boolean");
            }

            @Override
            public Number getAsNumber() {
                throw wrong(CvType.OBJECT, "number");
            }

            @Override
            public String getAsString() {
                throw wrong(CvType.OBJECT, "string");
            }

            @Override
            public CvArray getAsArray() {
                throw wrong(CvType.OBJECT, "array");
            }

            @Override
            public CvObject getAsObject() {
                return this;
            }

            @Override
            public CvType getType() {
                return CvType.OBJECT;
            }
        }
    }
}
